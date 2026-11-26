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
    fields(request, ('schema_version','quality_report','token_report','evidence'), ('native_request','tensor_alignment'))
    if request['schema_version'] != '1.0' or not isinstance(request['evidence'], list):
        raise ValueError('张量对齐需要 1.0 版请求与证据列表')
    for source in (request['quality_report'], request['token_report'], *request['evidence']):
        reference(source)
    if 'native_request' in request and not isinstance(request['native_request'], dict):
        raise ValueError('原生复测请求无效')
    validate_policy(request.get('tensor_alignment', {}))
    return request


def validate_policy(policy):
    if not isinstance(policy, dict) or policy.keys() - {'atol','rtol'}:
        raise ValueError('张量容差仅接受 atol、rtol')
    if any(type(value) not in (int,float) or not 0 <= value <= float_info.max for value in policy.values()):
        raise ValueError('张量容差必须为有限非负数值')


def aggregate(rows):
    states = {row['result'] for row in rows} - {'not_triggered'}
    if not states:
        return 'not_triggered'
    if 'mismatch' in states:
        return 'mismatch'
    return 'aligned' if states == {'aligned'} else 'insufficient_evidence'


def target_status(result):
    return {'aligned': 'pass', 'mismatch': 'fail'}.get(result, 'not_evaluated')


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
        row['target_status'] = target_status(row['result'])
    result = aggregate(rows)
    verdict = {'comparison_result': result, 'alignment_status': result, 'target_status': target_status(result)}
    report = {key: manifest[key] for key in ('schema_version','task_id','operation','run_id')}
    report.update(quality_report=request['quality_report'], token_report=request['token_report'], cases=rows,
                  uncovered=sources.issues, **verdict)
    report_path = folder / 'tensor-report.json'
    write(report_path, report)
    manifest.update(status='completed', success=True, finished_at=now(), tensor_report=ref(report_path),
                    **verdict, plans=[row['plan'] for row in rows if row.get('plan')],
                    npu_bundles=[row['npu'] for row in rows if row.get('npu_capture_status') == 'complete'])
    write(path, manifest)
    return 0, {k: manifest[k] for k in ('task_id','operation','run_id','status','manifest','alignment_status',
                                         'plans','npu_bundles','tensor_report','target_status','comparison_result')}
