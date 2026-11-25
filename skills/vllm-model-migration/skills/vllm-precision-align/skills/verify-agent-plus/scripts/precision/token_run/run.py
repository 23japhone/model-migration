"""从首轮原生协议和选题结果运行二遍测评，分别保留每题的证据。"""
from .. import contract
from ..evidence import begin, bound, finish, record, verify_files, write
from ..inputs import collect
from ..quality import runtime, static_gate
from ..quality.contract import run_request
from ..quality.protocol import check_assets, ref, stable_ids, subset_assets
from ..quality.run import ERRORS, freeze
from ..runtime import service
from .capture import session
from .measurement import measure_case


def validate(request, action='token-run'):
    required = ('schema_version', 'native_request', 'selection', 'quality_report')
    contract.object_fields(request, (*required, 'tensor_plans') if action == 'tensor-run' else required)
    if action == 'tensor-run':
        if not isinstance(request['tensor_plans'], list) or not request['tensor_plans']:
            raise ValueError('三遍重放需要非空的逐题采集清单')
        for source in request['tensor_plans']:
            contract.reference(source)
    if request['schema_version'] != '1.0':
        raise ValueError('token-run 请求版本无效')
    for key in ('selection', 'quality_report'):
        contract.reference(request[key])
    run_request(request['native_request'])


def first_measurement(report):
    refs = report['evidence']
    measurement = refs['measurement']
    receipt = bound(refs.get('verification') or refs['receipt'])
    if (receipt.get('task_id') != 'verify-agent-plus' or receipt.get('status') != 'completed'
            or receipt['conclusions'].get('evaluation_validity') != 'pass'
            or receipt['detailed_report'].get('hardware') != 'npu'
            or receipt['evidence'].get('quality_evidence') != measurement):
        raise ValueError('首轮 NPU 质量测量与原生回执不匹配')
    original = bound(measurement)
    if (original.get('hardware'), original.get('format')) != ('npu', 'task-quality-native-v1'):
        raise ValueError('缺少首轮 NPU 质量测量协议')
    return original


def selection(request):
    selected = bound(request['selection'])
    report = bound(request['quality_report'])
    protocol = request['native_request']['protocol']
    first = first_measurement(report)
    if first['protocol'] != protocol:
        raise ValueError('定向重跑与原始质量测量协议不同')
    if selected['quality_report'] != request['quality_report'] or selected['dataset'] != protocol['dataset']:
        raise ValueError('选题不属于当前质量报告与数据集')
    failed = [r for r in report['scores'] if r.get('precision_target') == 'fail' and r.get('comparability') == 'pass']
    if ((report.get('task_id'), report.get('operation'), report.get('status')) != ('vllm-precision-compare', 'run', 'completed')
            or report['conclusions'].get('precision_target') != 'fail'
            or report['baseline']['dataset'] != selected['dataset'] or not failed
            or report['conclusions']['evaluation_validity'] != 'pass'):
        raise ValueError('缺少可比且明确未达标的首轮测量')
    return checked_rows(selected['cases'], failed), first


def checked_rows(rows, failed):
    keys = [(r['subset'], r['sample_id']) for r in rows]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError('定向重跑题号为空或重复')
    failed_scopes = {(row['metric'], row['subset'], row['direction']) for row in failed}
    for row in rows:
        if type(row['sample_id']) not in (str, int) or not row['triggers']:
            raise ValueError('定向重跑题号或失败依据无效')
        for trigger in row['triggers']:
            scope = trigger['subset']
            if (trigger['metric'], scope, trigger['direction']) not in failed_scopes or scope not in (None, row['subset']):
                raise ValueError('题目超出未达标评分项范围')
            if trigger['selection_basis'] != 'regressed_sample' and (
                    trigger['selection_basis'] != 'failed_subset_roster' or scope is None):
                raise ValueError('整科重跑必须由该科已核验的失败分项触发')
    return rows


def prepare(native, inputs, assets, attempt, first):
    before = service.snapshot(inputs, native['measurement'], attempt, 'before')
    snapshot = freeze(native, inputs, assets, attempt, before)
    current = runtime.recorded(snapshot, before)
    static_gate.check(native, current)
    original = first.get('static_conditions') or {}
    for field, observed in original.items():
        if observed is not None and current.get(field) != observed:
            raise ValueError('二遍测评与首轮的实际运行条件不同：' + field)
    verify_files({**snapshot['files'], **snapshot['validation_files']})
    ids = {name: {v: i for i, v in enumerate(stable_ids(asset))} for name, asset in assets.items()}
    tasks = {t['subset']: t for t in snapshot['tasks']}
    source = native['measurement']['deployment']
    if inputs['deployment_report'].get('operation') == 'review':
        source = inputs['deployment_report']['inputs']['source']
    return {'raw': source, 'before': before, 'ids': ids, 'tasks': tasks}


def capture_case(folder, request, row, prepared):
    native = request['native_request']
    subset = row['subset']
    index = prepared['ids'][subset][row['sample_id']]
    folder.mkdir()
    with session(folder, prepared['raw']) as source:
        record_ref, metrics = measure_case(folder, native, source, row, prepared['tasks'][subset], index)
    identity = {'subset': subset, 'sample_id': row['sample_id']}
    path = folder / 'review.json'
    write(path, {**identity, 'hardware': 'npu', 'evidence_kind': 'runtime', 'metrics': metrics,
                 'record_sha256': record_ref['sha256'], 'quality_report_sha256': request['quality_report']['sha256']})
    return {**identity, 'record': record_ref, 'review': ref(path)}


def collect_cases(attempt, request, rows, prepared):
    cases, issues = [], []
    for ordinal, row in enumerate(rows):
        try:
            cases.append(capture_case(attempt / f'case-{ordinal}', request, row, prepared))
        except (*ERRORS, IndexError) as error:
            issues.append({'subset': row['subset'], 'sample_id': row['sample_id'], 'message': str(error)})
    return cases, issues


def run(request, parent, run_id):
    native = request['native_request']
    action = 'tensor-run' if 'tensor_plans' in request else 'token-run'
    try:
        rows, first = selection(request)
        if action == 'tensor-run':
            from .tensor import bind_plans
            rows = bind_plans(request, rows)
        inputs = collect(native['measurement'])
        assets = subset_assets(native)
        contract.check_output(parent, inputs['protected'] + [a['path'] for a in assets.values()])
        check_assets(native['protocol'], inputs['identity']['model_files'], assets)
    except ERRORS as error:
        manifest = record(request, run_id, action, 'blocked')
        manifest['summary'] = str(error)
        return 3, manifest
    attempt, manifest = begin(parent, request, run_id, action)
    try:
        prepared = prepare(native, inputs, assets, attempt, first)
        cases, issues = collect_cases(attempt, request, rows, prepared)
        manifest['detailed_report']['case_errors'] = issues
        after = service.snapshot(inputs, native['measurement'], attempt, 'after')
        if prepared['before'] != after or collect(native['measurement'])['identity'] != inputs['identity'] or assets != subset_assets(native):
            raise ValueError('定向重跑期间服务、权重或数据发生变化')
        if action == 'tensor-run':
            manifest['evidence']['tensor_bundles'] = [row['record'] for row in cases]
            manifest['evidence']['replay_reviews'] = [row['review'] for row in cases]
        elif cases:
            path = attempt / 'token-bundle.json'
            write(path, {'format': 'token-prediction-bundle-v1', 'hardware': 'npu', 'evidence_kind': 'runtime',
                         'quality_report_sha256': request['quality_report']['sha256'], 'cases': cases})
            manifest['evidence']['token_bundle'] = ref(path)
        manifest['conclusions']['evaluation_validity'] = 'fail' if issues else 'pass'
        return finish(manifest, 'failed' if issues else 'completed')
    except (*ERRORS, KeyboardInterrupt) as error:
        manifest['diagnostic'] = {'type': type(error).__name__, 'message': str(error)}
        return finish(manifest, 'failed')
