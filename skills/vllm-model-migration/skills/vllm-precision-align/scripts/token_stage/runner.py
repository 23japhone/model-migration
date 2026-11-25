"""从任务质量失败报告选择样本，消费已取得的同轮 GPU/NPU 预测。"""
from sys import float_info
from uuid import uuid4

from alignment.evidence import bound, now, ref, write
from alignment.request import fields, output, reference
from .bundles import index_bundles
from .comparison import aggregate, process_case
from .cpu_model import optional_model
from .measurement import collect_npu
from .native import NativeScores
from .selection import case_key, failing_rows, select_rows


def validate(request):
    fields(request, ('schema_version', 'quality_report', 'token_alignment'), ('bundles', 'native_request'))
    if request['schema_version'] != '1.0':
        raise ValueError('Token 阶段仅支持版本 1.0')
    reference(request['quality_report'])
    policy = request['token_alignment']
    fields(policy, ('raw_logits', 'processed_scores'))
    for options in policy.values():
        fields(options, ('atol',))
        tolerance = options['atol']
        if type(tolerance) not in (int, float) or not 0 <= tolerance <= float_info.max:
            raise ValueError('Token 绝对容差必须是非负有限数值')
    if 'native_request' in request and not isinstance(request['native_request'], dict):
        raise ValueError('定向 NPU 评测须提供冻结的原生请求')
    sources = request.get('bundles', [])
    if not isinstance(sources, list):
        raise ValueError('bundles 须为两端证据引用数组')
    for source in sources:
        reference(source)
    return request


def rows_need_cpu_model(selected, indexed):
    for item in selected['cases']:
        entries = [indexed.get((side, case_key(item))) for side in ('gpu', 'npu')]
        if all(entries) and not any(entry.get('cpu') for entry in entries):
            return True
    return False


def run(request, work_root, run_id, action):
    source = request['quality_report']
    report = bound(source)
    failures = failing_rows(report)
    native = NativeScores(report, bool(failures))
    selected = select_rows(failures, native, source, report['baseline']['dataset'])
    attempt = output(work_root, run_id) / 'token' / uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    path = attempt / 'token-stage-manifest.json'
    selection = attempt / 'selection.json'
    write(selection, selected)
    manifest = {'schema_version': '1.0', 'task_id': 'vllm-precision-align', 'operation': action,
                'run_id': run_id, 'manifest': str(path), 'status': 'running', 'started_at': now(),
                'inputs': request, 'selection': ref(selection), 'selected_count': len(selected['cases'])}
    write(path, manifest)
    keys = {case_key(item) for item in selected['cases']}
    indexed, uncovered = index_bundles(request.get('bundles', []), source['sha256'], keys)
    measurement = collect_npu(request, selected, manifest['selection'], indexed, attempt, run_id, native.npu.get('protocol'))
    if measurement and measurement.get('bundle'):
        fresh, issues = index_bundles([measurement['bundle']], source['sha256'], keys)
        indexed.update(fresh)
        uncovered.extend(issues)
    cpu_model = optional_model(request, report, native) if rows_need_cpu_model(selected, indexed) else None
    rows = [process_case(case, index, indexed, attempt, request, cpu_model)
            for index, case in enumerate(selected['cases'])]
    state = aggregate(rows, selected) if failures else 'not_triggered'
    payload = {'schema_version': '1.0', 'task_id': 'vllm-precision-align', 'operation': action,
               'quality_report': source, 'selection': manifest['selection'], 'alignment_status': state,
               'cases': rows, 'uncovered': uncovered + selected['uncovered'],
               'comparison_scope': 'provided_predictions'}
    if measurement:
        payload['npu_measurement'] = measurement
    result = attempt / 'token-report.json'
    write(result, payload)
    manifest.update(status='completed', success=True, alignment_status=state, token_report=ref(result),
                    finished_at=now())
    write(path, manifest)
    return 0, {k: manifest[k] for k in ('task_id', 'run_id', 'status', 'manifest', 'alignment_status',
                                        'selection', 'token_report', 'selected_count')}
