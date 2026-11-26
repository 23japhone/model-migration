"""绑定原始 NPU 测量，重读历史配置、报告和 reviews；不发起模型请求。"""
from copy import deepcopy
from pathlib import Path

from ..contract import check_output
from ..evidence import begin, bound, finish, load, record, verify_files
from ..history import process_succeeded
from ..inputs import deployment
from ..runtime.evalscope.configuration import load_config, validate
from . import runtime
from .native import measure
from .protocol import check_assets, check_config, ref, stable_ids, subset_assets
from .run import ERRORS, fail, project


def source_run(request):
    ref_ = request['source']
    original = bound(ref_)
    if (original.get('task_id') != 'verify-agent-plus' or original.get('status') != 'completed'
            or original['conclusions']['evaluation_validity'] != 'pass'
            or original['detailed_report'].get('hardware') != 'npu'
            or original.get('operation') not in ('quality-run', 'run') or original['report_path'] != ref_['path']):
        raise ValueError('来源须为原始完成且有效的 NPU 测量回执')
    return original


def semantic_config(raw):
    result = deepcopy(raw)
    for field in ('work_dir','dataset_dir','api_url','api_key','model','model_id','no_timestamp','collect_perf','debug'):
        result.pop(field, None)
    for args in result['dataset_args'].values():
        args.pop('local_path', None)
    return result


def expected_context(request, original_raw):
    ev = request['measurement']['evaluator']
    if ref(ev['config']['path']) != ev['config']:
        raise ValueError('期望配置已变化')
    raw = load_config(ev['config']['path'])
    if semantic_config(raw) != semantic_config(original_raw):
        raise ValueError('既有测量的有效任务、样本或生成配置不匹配')
    _, _, handoff = deployment(request['measurement']['deployment'])
    validate(raw, request['measurement']['tasks'], handoff, allow_native_budget=True)
    assets = subset_assets(request)
    check_assets(request['protocol'], handoff['model']['files'], assets)
    return {name: stable_ids(asset) for name, asset in assets.items()}


def check_historical_config(expected, raw, actual, environment):
    digests = set(environment['configuration_sources'].values())
    if len(digests) != 1:
        raise ValueError('原生 benchmark 的评分源码不唯一')
    check_config(expected['protocol'], raw, actual, expected.get('scoring_proof'), digests.pop())


def source_integrity(root, files):
    expected = ('quality-index.json', 'original-config.json', 'effective-config.json', 'environment.json',
                'process-scope/process.json', 'service-before.json', 'service-after.json')
    for item in expected:
        path = root / item
        if str(path) not in files:
            raise ValueError('原始测量缺少绑定证据：' + item)
    if load(root/'service-before.json') != load(root/'service-after.json'):
        raise ValueError('原生测量前后的服务身份不一致')
    config = load(root/'effective-config.json')
    if config.get('collect_perf') is not False or config.get('debug') is not False:
        raise ValueError('历史质量测量未固定纯评分配置')
    process = load(root/'process-scope/process.json')
    if (not process_succeeded(process)
            or process.get('argv', [])[-2:] != ['run', str(root/'effective-config.json')]):
        raise ValueError('原生测量进程及实际配置未完成绑定')


def check_original_coverage(original, expected):
    details = original['detailed_report']
    root = Path(original['report_path']).parent
    source_integrity(root, details['files'])
    if expected:
        raw = load(root/'original-config.json')
        current = expected_context(expected, raw)
        env = load(root/'environment.json')
        check_historical_config(expected, raw, load(root/'effective-config.json'), env)
        if current != details['stable_samples']:
            raise ValueError('当前及历史稳定样本不匹配')
    index = load(root/'quality-index.json')
    if details['tasks'] != index['tasks'] or details.get('context_validation') != 'pass':
        raise ValueError('原始测量的有效配置、范围或运行身份未验证')


def check_static_conditions(original, stored, result, expected):
    current_index = load(Path(original['report_path']).parent/'quality-index.json')
    snapshot = Path(original['report_path']).parent/'service-before.json'
    before = load(snapshot)
    result['static_conditions'] = runtime.recorded(current_index, before)
    if expected:
        runtime.check_deployment(original['inputs']['measurement']['deployment'],
                                 expected['measurement']['deployment'], before['generation'])
    saved = stored.get('static_conditions')
    required = {'chat_template_sha256', 'tokenizer_sha256', 'server_generation'}
    if not isinstance(saved, dict) or not required <= saved.keys() or any(
            result['static_conditions'].get(k) != v for k,v in saved.items()):
        raise ValueError('历史服务默认值、模板或实际请求证据不一致')


def quality(original, expected):
    details = original['detailed_report']
    verify_files(details['files'])
    original_spec = original['inputs']
    historical = original_spec['protocol']
    protocol = expected['protocol'] if expected else historical
    if {k: v for k,v in historical.items() if k != 'metrics'} != {k: v for k,v in protocol.items() if k != 'metrics'}:
        raise ValueError('历史质量测量与期望的任务身份或执行口径不同')
    check_original_coverage(original, expected)
    args = (details['scope_root'], details['tasks'], details['stable_samples'])
    original_result = measure(*args, original_spec['scores'])
    stored = bound(original['evidence']['quality_evidence'])
    if (stored['scores'] != original_result['scores'] or stored['protocol'] != historical
            or stored['hardware'] != 'npu' or stored['format'] != 'task-quality-native-v1'):
        raise ValueError('原测量结果与重新读取的原生证据不同')
    result = measure(*args, expected['scores']) if expected and expected['scores'] != original_spec['scores'] else original_result
    if any(details['files'].get(k) != v for k,v in result['files'].items()):
        raise ValueError('原生证据未完整绑定在原测量中')
    check_static_conditions(original, stored, result, expected)
    result['stable_samples'] = details['stable_samples']
    return protocol, result


def original_config(original, files):
    folder = Path(original['report_path']).parent
    candidates = [Path(name) for name in files if Path(name).parent == folder and Path(name).stem == 'source-config']
    if len(candidates) != 1:
        raise ValueError('缺少唯一的已固定原配置')
    verify_files({str(candidates[0]): files[str(candidates[0])]})
    return load_config(candidates[0])


def check_version(conditions, p):
    revisions = {row.get('evaluation_version') or conditions['evaluator_version'] for row in conditions['datasets']}
    if revisions != {p['task_revision']}:
        raise ValueError('历史与请求的 benchmark evaluation_version 不同')
    if conditions['evaluator_backend'] != 'evalscope' or conditions['evaluator_version'] != p['evaluator_version']:
        raise ValueError('工具或版本不一致')


def ordinary_conditions(original, expected):
    details = original['detailed_report']
    conditions = details['conditions']
    p = expected['protocol']
    check_version(conditions, p)
    raw = original_config(original, details['files'])
    stable = expected_context(expected, raw)
    tasks = details['tasks']
    if [t['subset'] for t in tasks] != p['subsets'] or {t['benchmark'] for t in tasks} != {p['dataset']}:
        raise ValueError('历史测量未覆盖指定范围')
    if len(tasks) > 1 and any(s['subset'] is None for s in expected['scores']):
        raise ValueError('历史测量按子集分别运行，缺少联合运行的原生总分')
    from ..inputs import canonical
    if (canonical(conditions['model_files']) != p['model_identity']
            or conditions['dataset']['sha256'] != p['dataset_identity']
            or any(p['sample_identities'][t['subset']] != conditions['selection'] for t in tasks)):
        raise ValueError('历史实际权重、数据文件或测量样本身份不一致')
    return p, raw, stable, tasks


def ordinary(original, expected):
    if not expected:
        raise ValueError('普通原测量需提供期望协议及指标映射')
    details = original['detailed_report']
    rows = details['measurements']
    by_task = {row['dataset']: row for row in rows}
    if not all(row.get('valid') and process_succeeded(row.get('process')) for row in rows) or details.get('context_validation') != 'pass':
        raise ValueError('不能复用存在无效项或服务身份缺口的历史结果')
    p, raw, stable, tasks = ordinary_conditions(original, expected)
    result = []
    for task in tasks:
        if task['dataset'] not in by_task:
            raise ValueError('原始回执缺少该子集的有效执行')
        verify_files({task['config']: details['files'][task['config']]})
        check_historical_config(expected, raw, load(task['config']), details['conditions']['evaluator_identity'])
        subset = task['subset']
        specs = [spec for spec in expected['scores'] if spec['subset'] in (None, subset)]
        observed = measure(task['output_root'], [task], stable, specs)
        if any(details['files'].get(k) != digest for k, digest in observed['files'].items()):
            raise ValueError('原生报告、样本或 reviews 与历史回执失去绑定')
        result.extend(observed['scores'])
    conditions = details['conditions']
    runtime.check_deployment(original['inputs']['deployment'], expected['measurement']['deployment'],
                             conditions['server_generation'])
    return p, {'scores': result, 'stable_samples': stable, 'static_conditions': runtime.ordinary(conditions)}


def analyze(request, parent, run_id):
    try:
        original = source_run(request)
        check_output(parent, [request['source']['path'], *original['detailed_report'].get('protected_roots', [])])
    except ERRORS as error:
        manifest = record(request, run_id, 'quality-analyze', 'blocked')
        manifest['summary'] = str(error)
        return 3, manifest
    attempt, manifest = begin(parent, request, run_id, 'quality-analyze')
    try:
        restore = quality if original['operation'] == 'quality-run' else ordinary
        protocol, result = restore(original, request.get('expected'))
        if ref(request['source']['path']) != request['source']:
            raise ValueError('历史测量回执在复核期间发生变化')
        manifest['detailed_report'].update(hardware='npu', formal_requests=0, stable_samples=result['stable_samples'])
        manifest['evidence']['source'] = request['source']
        project(attempt, manifest, protocol, result)
        return finish(manifest, 'completed')
    except ERRORS as error:
        return fail(manifest, error, 'blocked')
