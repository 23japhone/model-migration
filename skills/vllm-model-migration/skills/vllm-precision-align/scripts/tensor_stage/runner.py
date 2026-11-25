"""张量与算子对齐阶段的输入、独立尝试及业务状态汇总。"""
from sys import float_info
from uuid import uuid4

from alignment.evidence import now, ref, write
from alignment.request import fields, output, reference, roots
from token_stage.selection import case_key
from .cases import process
from .scope import Sources
from .targets import upstream


def validate(request):
    fields(request, ('schema_version','quality_report','token_report','tensor_alignment','evidence'), ('native_request',))
    if request['schema_version'] != '1.0' or not isinstance(request['evidence'], list):
        raise ValueError('张量对齐需要 1.0 版请求与证据列表')
    for source in (request['quality_report'], request['token_report'], *request['evidence']):
        reference(source)
    if 'native_request' in request and not isinstance(request['native_request'], dict):
        raise ValueError('原生复测请求无效')
    validate_policy(request['tensor_alignment'])
    return request


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) not in (set(), {'atol','rtol'}):
        raise ValueError('张量容差须为空或同时提供 atol、rtol')
    if any(type(value) not in (int,float) or not 0 <= value <= float_info.max for value in policy.values()):
        raise ValueError('张量容差必须为有限非负数值')


def aggregate(rows):
    active = [row for row in rows if row['result'] != 'not_triggered']
    if not active:
        return 'not_triggered'
    states = {row['result'] for row in active}
    if len(states) == 1:
        return next(iter(states))
    if states.issubset({'aligned','mismatch'}):
        return 'mismatch'
    return 'partially_evaluated'


def run(request, work_root, run_id, action):
    quality, token, selected = upstream(request)
    protected = [path for path in roots(request.get('native_request', {})) if path.is_dir()]
    folder = output(work_root, run_id, protected) / 'tensor' / uuid4().hex
    folder.mkdir(parents=True)
    path = folder / 'tensor-stage-manifest.json'
    manifest = {'schema_version': '1.0', 'task_id': 'vllm-precision-align', 'run_id': run_id, 'operation': action,
                'manifest': str(path), 'started_at': now(), 'status': 'running', 'inputs': request}
    write(path, manifest)
    sources = Sources(request['evidence'])
    context = {'quality': quality, 'request': request, 'run_id': run_id, 'sources': sources}
    rows = []
    for index, original in enumerate(token['cases']):
        case_dir = folder / f'case-{index}'
        case_dir.mkdir()
        rows.append(process(context, original, selected[case_key(original)], case_dir))
    for row in rows:
        kinds = [*row['prediction_evidence_kind'].values(), *row['evidence_kind'].values()]
        row['simulated'] = 'synthetic' in kinds
    simulated = any(row['simulated'] for row in rows)
    comparison_result = aggregate(rows)
    state = 'simulated' if simulated and comparison_result in ('mismatch', 'aligned', 'not_evaluated') else comparison_result
    report = {key: manifest[key] for key in ('schema_version','task_id','operation','run_id')}
    report.update(quality_report=request['quality_report'], token_report=request['token_report'], cases=rows,
                  uncovered=sources.issues, simulated=simulated, comparison_result=comparison_result, alignment_status=state)
    report_path = folder / 'tensor-report.json'
    write(report_path, report)
    manifest.update(status='completed', success=True, finished_at=now(), tensor_report=ref(report_path),
                    simulated=simulated, comparison_result=comparison_result, alignment_status=state, plans=[row['plan'] for row in rows if row.get('plan')],
                    npu_bundles=[row['npu'] for row in rows if row.get('npu_capture_status') == 'complete'])
    write(path, manifest)
    return 0, {k: manifest[k] for k in ('task_id','operation','run_id','status','manifest','alignment_status',
                                         'plans','npu_bundles','tensor_report','simulated','comparison_result')}
