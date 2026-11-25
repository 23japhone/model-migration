"""精度阶段的质量评测入口，每个客户基线独立交付。"""
from functools import partial
from time import monotonic
import uuid

from alignment.evidence import EvidenceError, digest, now, ref, safe, write
from alignment.request import fields, output, reference, roots
from .children import Children, ConditionsMismatch
from .measurement import select
from .discovered import recheck


def validate(request):
    fields(request, ('schema_version', 'baseline', 'native_request'), ('gpu_evidence', 'candidates', 'timeout_seconds', 'require_reuse'))
    if request['schema_version'] != '1.0' or not isinstance(request['native_request'], dict):
        raise ValueError('质量任务需要版本 1.0 和原生评测请求')
    baseline = request['baseline']
    if not isinstance(baseline, dict) or baseline.get('evaluator') != 'evalscope' or not baseline.get('scores'):
        raise ValueError('首版质量任务需要 EvalScope 基线及评分项')
    candidates = request.get('candidates', [])
    if not isinstance(candidates, list):
        raise ValueError('历史候选须为引用数组')
    for source in candidates:
        reference(source)
    if request.get('gpu_evidence') is not None:
        reference(request['gpu_evidence'])
    if type(request.get('require_reuse', False)) is not bool:
        raise ValueError('require_reuse 须为布尔值')
    timeout = request.get('timeout_seconds', 14400)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('超时须为 1 至 86400 秒')
    return request


def historical(source, uncovered):
    if not source:
        return None
    try:
        if digest(source['path']) != source['sha256']:
            raise ValueError('GPU 历史来源摘要已变化')
    except (OSError, ValueError) as error:
        uncovered.append(safe(error))
        return None
    return source


def select_measurement(request, children):
    recheck(request, children.inspect_gpu)
    inspect_before_run = partial(children.inspect_gpu, label='inspect-gpu-before-rerun')
    return select(request['native_request'], request.get('candidates', []), children.native,
                  require_reuse=request.get('require_reuse', False),
                  before_fallback=partial(recheck, request, inspect_before_run))


def run(request, work_root, run_id, action):
    parent = output(work_root, run_id, roots(request['native_request']))
    attempt = parent / 'quality' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    path = attempt / 'quality-stage-manifest.json'
    value = {'schema_version': '1.0', 'task_id': 'vllm-precision-align', 'run_id': run_id,
             'operation': action, 'inputs': request, 'status': 'running', 'started_at': now(),
             'manifest': str(path), 'uncovered': []}
    write(path, value)
    children = Children(attempt, run_id, request.get('timeout_seconds', 14400))
    started = monotonic()
    try:
        measured = select_measurement(request, children)
        value.update(measured)
        write(path, value)
        gpu = historical(request.get('gpu_evidence'), value['uncovered'])
        value['quality_report'] = children.compare(request['baseline'], measured, gpu)
        value.update(status='completed', success=True)
    except ConditionsMismatch as error:
        cause = safe(error)
        report = attempt / 'quality-report.json'
        write(report, {'baseline': request['baseline'], 'quality_measurement_started': False, 'formal_requests': 0,
                       'conclusions': {'evaluation_validity': 'not_evaluated', 'comparability': 'not_evaluated',
                                       'precision_target': 'not_evaluated'}, 'uncovered': [cause]})
        value.update(status='completed', success=True, alignment_status='not_evaluated',
                     quality_report=ref(report), uncovered=[cause], formal_requests=0)
    except EvidenceError:
        raise
    except (ValueError, KeyError, OSError, TypeError, RuntimeError, KeyboardInterrupt) as error:
        value.update(status='failed', success=False, diagnostic=safe(error) or type(error).__name__)
    value.update(finished_at=now(), duration_seconds=monotonic() - started)
    write(path, value)
    keys = ('task_id', 'run_id', 'status', 'manifest', 'source', 'receipt', 'verification',
            'measurement', 'quality_report', 'duration_seconds', 'alignment_status', 'formal_requests', 'diagnostic')
    summary = {key: value[key] for key in keys if key in value}
    return (0 if value['success'] else 1), summary
