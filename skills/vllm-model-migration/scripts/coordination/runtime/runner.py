"""按真实依赖编排阶段，失败保留独立成果，最后委托资源所有者收尾。"""
import copy
from pathlib import Path
import signal

from ..planning import STAGES
from . import aggregate, bindings, receipts, service_lifecycle, sources
from .delegation import delegate
from .evidence import EvidenceError, begin, publish, save
from .preflight import Blocked, preflight


def stage_request(key, values, request, attempt, manifest):
    value = copy.deepcopy(values[key])
    if key == 'migration':
        if manifest['stages']['preparation']['status'] != 'completed':
            raise Blocked('准备未完成，迁移缺少必需前置')
        value['preparation'] = manifest['stages']['preparation']['receipt']
    if key == 'precision':
        value = bindings.precision(request, value, manifest, attempt)
        if not value['evaluations']:
            raise Blocked('全部评测的显式依赖未完成')
    return value


def perform(key, values, request, attempt, manifest, work_root, protected):
    row = manifest['stages'][key]
    row['status'] = 'running'
    save(attempt, manifest)
    try:
        if key == 'migration' and 'result' in request['stages'][key]:
            source = request['stages'][key]['result']
            value = sources.bound(source)
            receipts.validate(value, Path(source['path']), key, STAGES[key][0])
            from .continuation import reusable
            reusable(value)
            code = receipts.expected_code(value, key)
            row.update(mode='reuse', input_snapshot=source)
        else:
            child_request = stage_request(key, values, request, attempt, manifest)
            value, source, code = delegate(key, child_request, attempt, work_root, manifest['run_id'],
                                          manifest['operation'], request.get('timeout_seconds', 14400), protected)
            row['input_snapshot'] = sources.ref(attempt/(key+'.request.json'))
        row.update(status=value['status'], receipt=source, exit_code=code,
                   conclusions=value['conclusions'], next_action=value.get('next_action', 'resolve_stage_outcomes'))
        aggregate.collect(manifest, key, value, source)
        if row.get('blocked_evaluations'):
            row.update(status='failed' if value['status']=='failed' else 'blocked', next_action='resolve_dependencies')
    except EvidenceError:
        raise
    except (Blocked, *sources.ERRORS, RuntimeError) as error:
        row.update(status='blocked' if isinstance(error, Blocked) else 'failed',
                   diagnostic={'type':type(error).__name__, 'message':str(error)}, next_action='resolve_stage_outcomes')
    save(attempt, manifest)


def cancelled(signum, frame):
    raise KeyboardInterrupt('总入口取消；阶段清理结果需复核')


def stages(request, values, errors, manifest, attempt, work_root, protected):
    for key in request['stages']:
        manifest['stages'][key] = {'status':'blocked' if key in errors else 'not_run',
                                  'skill':STAGES[key][0], 'source':request['stages'][key]}
        if key in errors:
            manifest['stages'][key]['diagnostic'] = {'type':'InputError', 'message':errors[key]}
    for key in STAGES:
        if key in manifest['stages'] and key not in errors:
            perform(key, values, request, attempt, manifest, work_root, protected)


def run(request, work_root, parent, run_id, action, source):
    if sources.bound(source) != request:
        raise ValueError('顶层请求读取后发生变化')
    work_root = Path(work_root).expanduser().absolute()
    values, errors, protected = preflight(request, work_root, action)
    protected.append(Path(source['path']))
    sources.protect(parent, protected)
    attempt, manifest = begin(request, parent, run_id, action, source)
    handlers = {sig:signal.signal(sig, cancelled) for sig in (signal.SIGINT, signal.SIGTERM)}
    interrupted = False
    try:
        try:
            stages(request, values, errors, manifest, attempt, work_root, protected)
        except KeyboardInterrupt:
            interrupted = True
            for row in manifest['stages'].values():
                if row['status'] in ('not_run', 'running'):
                    row.update(status='failed', diagnostic={'type':'Cancelled','message':'阶段清理未确认，资源状态未知'})
        aggregate.conclude(manifest)
        if interrupted:
            manifest.update(status='failed', success=False, next_action='inspect_resources_after_cancellation')
        service_lifecycle.finalize(request, manifest, attempt, work_root, protected, cancelled=interrupted)
        return publish(attempt, manifest)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
