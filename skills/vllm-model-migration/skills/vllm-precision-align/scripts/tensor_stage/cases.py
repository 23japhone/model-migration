"""逐题配对第三遍现场，先验 GPU，随后按需申请单次 NPU 原生测量。"""
from alignment.evidence import EvidenceError, bound, ref, safe, write
from token_stage.bundles import DATA_ERRORS

from . import delegation, reproduction, scope, versions
from .targets import make_target


def inspected(folder, source, row, plan, policy, side, *, require_complete=False):
    request = {'plan': row['plan'], side: source, 'tensor_alignment': policy}
    data = bound(source)
    reproduction.check_prefix(data, plan, side)
    report, row[side + '_preflight'] = delegation.compare(folder, side + '-preflight', 'tensor', request)
    if require_complete and report['capture_status'] != 'complete':
        row[side + '_capture_status'] = report['capture_status']
        return None
    prediction = reproduction.sample(folder, plan, source, data)
    row[side + '_prediction_replay'] = reproduction.check_single(folder, plan, prediction, side)
    row[side + '_capture_status'] = report['capture_status']
    return prediction


def obtain_npu(context, case, plan, plan_ref, folder, row):
    native = context['request'].get('native_request')
    if not native:
        row.update(result='awaiting_npu', reason='缺少 NPU 张量包或冻结的原生复测请求')
        return None
    if versions.missing(plan.get('runtime_versions', {}), {'gpu':'synthetic','npu':'runtime'}):
        row.update(result='needs_scope', reason='NPU 原生复测前须在清单冻结 torch、vllm、msprobe 实际版本')
        return None
    if native.get('protocol') != plan['protocol']:
        row.update(result='invalid_npu', reason='第三遍原生请求与首轮冻结协议不同')
        return None
    selection = {'format':'token-case-selection-v1', 'quality_report':plan['quality_report'],
                 'dataset': plan['protocol']['dataset'], 'cases':[case]}
    path = folder / 'selection.json'
    write(path, selection)
    source, receipt = delegation.capture(folder, context['run_id'], native, ref(path), plan_ref)
    row['npu_measurement'] = receipt
    return source


def inspect_npu(folder, row, source, plan, policy):
    row['npu'] = source
    row['evidence_kind']['npu'] = bound(source).get('evidence_kind')
    return inspected(folder, source, row, plan, policy, 'npu')


def choose_npu(context, case, folder, row, plan, supplied, gpu_complete):
    policy = context['request']['tensor_alignment']
    fallback = None
    if supplied:
        try:
            fallback = inspect_npu(folder, row, supplied, plan, policy)
        except (EvidenceError, reproduction.ReplayMismatch):
            raise
        except (*DATA_ERRORS, RuntimeError, IndexError) as exc:
            row['npu_import_issue'] = safe(exc)
        if row.get('npu_capture_status') == 'complete' or not context['request'].get('native_request'):
            return fallback
    if not gpu_complete:
        return fallback
    # 本次用户已给原生复测请求时，仅对这一题补无效或缺少的 NPU 证据。
    try:
        source = obtain_npu(context, case, plan, row['plan'], folder, row)
    except EvidenceError:
        raise
    except (*DATA_ERRORS, RuntimeError) as exc:
        row['npu_measurement_issue'] = safe(exc)
        return fallback
    if source is None:
        return fallback
    attempt = folder / 'new-npu'
    attempt.mkdir()
    previous = dict(row)
    try:
        return inspect_npu(attempt, row, source, plan, policy)
    except (EvidenceError, reproduction.ReplayMismatch):
        raise
    except (*DATA_ERRORS, RuntimeError, IndexError) as exc:
        row.update(previous)
        row['npu_measurement_issue'] = safe(exc)
        return fallback


def evaluate(context, case, folder, row):
    sources = context['sources']
    plan_ref = row['plan']
    plan = bound(plan_ref)
    policy = context['request']['tensor_alignment']
    gpu, _ = sources.bundle(plan_ref, plan, 'gpu')
    if not gpu:
        row.update(result='awaiting_gpu', reason='等待与本题计划摘要一致的 GPU 张量包')
        return
    row.update(result='invalid_gpu', gpu=gpu)
    row['evidence_kind']['gpu'] = bound(gpu).get('evidence_kind')
    npu, _ = sources.bundle(plan_ref, plan, 'npu')
    gpu_sample = inspected(folder, gpu, row, plan, policy, 'gpu', require_complete=not npu)
    if gpu_sample is None:
        row['reason'] = 'GPU 张量包没有覆盖计划中的完整有效槽位'
        return
    row['result'] = 'invalid_npu'
    npu_sample = choose_npu(context, case, folder, row, plan, npu, row['gpu_capture_status'] == 'complete')
    if not npu_sample:
        return
    row['reproduction'] = reproduction.check(folder, plan, gpu_sample, npu_sample)
    if row['reproduction']['status'] != 'reproduced':
        row['result'] = row['reproduction']['status']
        return
    request = {'plan': plan_ref, 'gpu': gpu, 'npu': row['npu'], 'tensor_alignment': policy}
    report, row['comparison'] = delegation.compare(folder, 'tensor-comparison', 'tensor', request)
    if report['result'] not in ('aligned','mismatch','insufficient_evidence','not_evaluated'):
        raise ValueError('双端张量结果无效')
    row.update(result=report['result'], capture_status=report['capture_status'])


def process(context, original, selected, folder):
    case = {k: selected[k] for k in ('subset','sample_id')}
    row = {**case, 'source_result': original['result'], 'result': 'insufficient_evidence',
           'prediction_evidence_kind': original.get('evidence_kind', {}), 'evidence_kind': {}}
    if original.get('result') != 'mismatch':
        row.update(result='not_triggered', reason='该题没有确认的双端同前文分数或选词异常')
        return row
    try:
        if original.get('triggers') != selected['triggers']:
            raise ValueError('二遍的退化题来源与选题清单不符')
        target = make_target(folder, (context['quality'], context['request']), case, original)
        selection = folder / 'target.json'
        write(selection, target)
        row.update(target=ref(selection), position=target['position'], comparison_token=original['comparison'])
        row['plan'], row['scope_uncovered'] = scope.prepare(context['sources'], target, folder)
        if row['plan'] is None:
            row.update(result='needs_scope', reason='等待实际调用清单或核验过的模型语义映射')
            return row
        evaluate(context, selected, folder, row)
    except EvidenceError:
        raise
    except reproduction.ReplayMismatch as exc:
        row.update(result='not_reproduced', reason=safe(exc), reproduction=exc.details)
    except (*DATA_ERRORS, RuntimeError, IndexError) as exc:
        row['reason'] = safe(exc)
    return row
