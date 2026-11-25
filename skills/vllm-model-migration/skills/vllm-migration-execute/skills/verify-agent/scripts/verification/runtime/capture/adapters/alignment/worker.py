"""仅在指定文本前向打开 msProbe，并在 execute_model 返回后恢复普通采集器。"""
from time import monotonic

from .gate import TargetGate
from .manifest import runtime_versions
from .plan import is_target, read_plan
from .. import state


def check_observer(owned):
    from msprobe.core.common.runtime import Runtime
    from msprobe.pytorch import PrecisionDebugger
    if PrecisionDebugger._instance is not owned or Runtime.is_running:
        raise ValueError('定向采集要求独占静止的 msProbe 实例')
    if owned is not None and owned.service.config.async_dump is not False:
        raise ValueError('定向 msProbe 采集器要求同步落盘')


def can_arm():
    try:
        check_observer(None)
    except (AttributeError, ImportError, ValueError):
        return False
    return True


def start(rec):
    frame = rec.frame
    intent = state.read(rec.folder / 'intent.json')
    digest = intent['tensor_plan']['sha256']
    if getattr(rec, 'target_plan_key', None) != (rec.folder, digest):
        _, rec.target_plan = read_plan(rec.folder / 'tensor-plan.json', digest)
        versions = runtime_versions()
        if rec.target_plan.get('runtime_versions', {}).get('npu') != versions:
            raise ValueError('NPU 的实际软件版本与计划不同')
        rec.target_versions = versions
        rec.target_plan_key = rec.folder, digest
    check_observer(rec.debugger)
    request = rec.runner.requests[frame['internal_id']]
    if getattr(request, 'mm_features', None):
        raise ValueError('定向采集仅支持纯文本请求')
    if not is_target(rec.target_plan, frame, request.prompt_token_ids, request.output_token_ids):
        return
    if (rec.folder / 'target-frame.json').exists():
        raise ValueError('同一请求多个前向命中指定位置')
    state.write(rec.folder / 'target-frame.json', {'forward_id': frame['forward_id'], 'plan_sha256': digest})
    debugger = rec.prepare_probe()
    check_observer(debugger)
    frame.update(targeted=True, versions=rec.target_versions)
    gate = TargetGate(rec.target_plan, rec.folder)
    rec.capture_start = monotonic()
    rec.target_gate = gate
    collector = debugger.service.data_collector
    hooks = gate.intercept(collector)
    hooks.__enter__()
    rec.target_hooks = hooks
    try:
        debugger.start(model=rec.runner.get_model(), rank_id=rec.rank)
        if debugger.service.data_collector is not collector or debugger.service.config.async_dump is not False:
            raise ValueError('msProbe 启动时变更了定向过滤采集器或同步写盘设置')
        frame['native_step'] = debugger.service.current_iter
    except Exception:
        # The runner records observer errors and continues inference. Disarm before it enters the model.
        close(rec)
        raise


def close(rec):
    hooks = getattr(rec, 'target_hooks', None)
    if hooks is None:
        return
    try:
        try:
            rec.debugger.stop()
        finally:
            rec.debugger.step()
    finally:
        hooks.__exit__(None, None, None)
        rec.target_hooks = None
        gate = rec.target_gate
        rec.frame.update(tensors=gate.rows, tensor_errors=gate.errors, capture_seconds=monotonic() - rec.capture_start)
        rec.target_gate = None


def requests_prediction(frame):
    return frame.get('profile') == 'token_prediction' or frame.get('targeted') is True
