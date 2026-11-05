"""核验 finalize 的原阶段与独立 review 证据链，不重新部署或判断语义。"""
import copy
import json
from pathlib import Path

from . import sources


def reviewed(source, raw_source, handoff, run_id=None):
    from .receipts import VERIFY_KEYS, common, original_evidence
    value = sources.bound(source)
    common(value, 'verify-agent')
    original_evidence(value)
    if value.get('operation') != 'review' or value['inputs'].get('source') != raw_source:
        raise ValueError('语义 review 未绑定原始 verify 回执')
    if value['status'] != 'completed' or value.get('success') is not True:
        raise ValueError('语义 review 未完成')
    if any(value['conclusions'].get(key) != 'pass' for key in VERIFY_KEYS):
        raise ValueError('语义 review 必需结论未全部通过')
    if run_id is not None and value.get('run_id') != run_id:
        raise ValueError('finalize 复验 run 身份不符')
    actual = review_handoff(value, raw_source)
    if actual != handoff:
        raise ValueError('语义 review 部署交接与原始 verify 不一致')
    sources.bound(actual)
    return value


def finalized(value):
    from .receipts import VERIFY_KEYS, validate
    inputs = value['inputs']
    if set(inputs) != {'source', 'review'} or value.get('review') != inputs['review']:
        raise ValueError('finalize 输入或 review 身份不符')
    source = sources.bound(inputs['source'])
    if 'source' in source['inputs']:
        raise ValueError('finalize 来源必须是原始运行阶段')
    validate(source, Path(inputs['source']['path']), 'migration', 'vllm-migration-execute')
    if source['status'] != 'blocked' or source.get('next_action') != 'semantic_review_required':
        raise ValueError('finalize 来源不是待语义复核阶段')
    raw_source = source['tasks']['verify']['receipt']
    raw = sources.bound(raw_source)
    if raw['status'] != 'completed' or raw['conclusions'].get('semantic') != 'review_required':
        raise ValueError('原始 verify 未处于语义复核状态')
    if any(raw['conclusions'].get(key) != 'pass' for key in VERIFY_KEYS-{'semantic'}):
        raise ValueError('原始 verify 存在其他未通过必需检查')
    approved = reviewed(inputs['review'], raw_source, source['deployment'])
    rechecked = reviewed(value['review_revalidated'], raw_source, source['deployment'], value['run_id'])
    if rechecked['inputs'] != approved['inputs']:
        raise ValueError('finalize 复验输入与批准 review 不一致')
    expected = copy.deepcopy(source['tasks'])
    expected['verify'].update(review=inputs['review'], conclusions=approved['conclusions'])
    if value['tasks'] != expected or any(value.get(key) != source.get(key) for key in ('route','deployment')):
        raise ValueError('finalize 更换了原始任务、路线或部署交接')
    return rechecked, sources.bound(source['tasks']['precheck']['receipt'])


def adaptation(value, precheck):
    row = value['tasks'].get('adapt', {})
    source = value['inputs'].get('adaptation')
    if row.get('status') != 'completed' or not source or row.get('receipt') != source:
        raise ValueError('适配路线缺少显式适配完成交接')
    result = sources.bound(source)
    if result.get('operation') != 'check' or result['conclusions'].get('adaptation') != 'pass':
        raise ValueError('适配交接未通过 check')
    context, previous = adaptation_context(result)
    if result['details'].get('errors') or context.get('errors'):
        raise ValueError('适配交接包含未解决错误')
    keys = ('selected_architecture','route','sources','model')
    if any(previous['details'].get(key) != precheck['details'].get(key) for key in keys):
        raise ValueError('适配交接与预检身份不符')
    if previous['inputs'].get('target') != precheck['inputs'].get('target'):
        raise ValueError('适配交接目标版本与预检不符')


def adaptation_context(result):
    details = result.get('details')
    if not isinstance(details, dict) or not isinstance(details.get('context'), dict):
        raise ValueError('适配详细结果与上下文须为对象')
    context = details['context']
    previous = sources.bound(context['precheck'])
    if any(not isinstance(previous.get(key), dict) for key in ('details', 'inputs')):
        raise ValueError('适配原预检的输入和详细结果须为对象')
    return context, previous


def reusable(value):
    """旧阶段输入与原始交接须保持可核验；服务现状交由精度任务复验。"""
    from .receipts import validate
    original = sources.bound(value['inputs']['source']) if 'source' in value['inputs'] else value
    inputs = original['inputs']
    for key in ('preparation', 'precheck', 'verify'):
        sources.bound(inputs[key])
    for key in ('register', 'adaptation'):
        if key in inputs:
            sources.bound(inputs[key])
    preparation = sources.bound(inputs['preparation'])
    validate(preparation, Path(inputs['preparation']['path']), 'preparation', 'vllm-migration-prepare')
    if preparation['status'] != 'completed' or preparation['inputs'].get('purpose') != 'deployment':
        raise ValueError('迁移来源未绑定完整部署准备')
    raw = sources.bound(original['tasks']['verify']['receipt'])
    if raw.get('operation') != 'verify':
        raise ValueError('迁移来源没有原始 verify 操作')
    handoffs = {'prepare':inputs['preparation']}
    for name, field in (('precheck','precheck'), ('adapt','adaptation'), ('register','registration')):
        row = original['tasks'].get(name, {})
        if row.get('receipt'):
            handoffs[field] = row['receipt']
    if raw['inputs'].get('handoffs') != handoffs:
        raise ValueError('原始 verify 未绑定准备、预检或适配注册交接')
    expected = {**sources.bound(inputs['verify']), 'handoffs':handoffs}
    if raw['inputs'] != expected:
        raise ValueError('原始 verify 输入与声明请求不一致')
    migration_inputs(original)


def migration_inputs(original):
    """按公开路径/默认值契约对照原任务，不重跑预检或注册。"""
    attempt = Path(original['evidence']['manifest']).parent
    precheck = sources.bound(original['tasks']['precheck']['receipt'])
    expected = precheck_request(sources.bound(original['inputs']['precheck']), attempt)
    if precheck['inputs'] != expected:
        raise ValueError('原始预检输入与声明请求不一致')
    row = original['tasks'].get('register', {})
    if row.get('receipt'):
        registered = sources.bound(row['receipt'])
        expected = registration_request(original, attempt)
        if registered['inputs'] != expected:
            raise ValueError('原始注册输入与声明请求不一致')


def normalized_path(value, base, *, resolve=True):
    path = base / Path(value).expanduser()
    return str(path.resolve() if resolve else path.absolute())


def precheck_request(value, attempt):
    """模型路径基于子任务 work-root，其他路径基于迁移冻结请求目录。"""
    expected = copy.deepcopy(value)
    expected.setdefault('features', ['eager'])
    if not isinstance(expected.get('sources', {}), dict):
        raise ValueError('原预检 sources 须为对象')
    for section in ('model', 'implementation'):
        if section in expected:
            base = attempt/'children/precheck' if section == 'model' else attempt
            expected[section]['path'] = normalized_path(expected[section]['path'], base)
    for source in expected.get('sources', {}).values():
        source['path'] = normalized_path(source['path'], attempt)
    for review in expected.get('reviews', []):
        if isinstance(review, dict) and review.get('evidence_file'):
            review['evidence_file'] = normalized_path(review['evidence_file'], attempt)
    if 'adaptation' in expected:
        reference = expected['adaptation']['reference']
        reference['path'] = normalized_path(reference['path'], attempt)
    if 'prepare_handoff' in expected:
        expected['prepare_handoff'] = normalized_path(expected['prepare_handoff'], attempt)
    return expected


def registration_request(original, attempt):
    declared = sources.bound(original['inputs']['register'])
    if 'register_container' in original['inputs']:
        return container_registration(original, attempt, declared)
    expected = copy.deepcopy(declared)
    precheck = original['tasks']['precheck']['receipt']
    expected['precheck'] = {'manifest':precheck['path'], 'sha256':precheck['sha256']}
    for section, key in (('model','config'), ('implementation','path'), ('target','registry_path')):
        expected[section][key] = normalized_path(expected[section][key], attempt)
    runtime = expected['runtime']
    runtime['python'] = normalized_path(runtime['python'], attempt, resolve=False)
    runtime['import_roots'] = [normalized_path(path, attempt) for path in runtime['import_roots']]
    if expected['mode'] == 'plugin':
        for key in ('project_root', 'install_site'):
            expected['plugin'][key] = normalized_path(expected['plugin'][key], attempt)
    return expected


def container_registration(original, attempt, declared):
    # 宿主不能猜测容器内链接解析；绑定复制前请求、原始容器映射及公开 plan。
    if sources.read(attempt/'register.request.json') != declared:
        raise ValueError('容器注册复制前请求与声明不一致')
    origin = sources.read(attempt/'register-origin.json')
    receipt = original['tasks']['register']['receipt']
    if origin.get('container') != original['inputs']['register_container'] or origin.get('local_receipt') != receipt:
        raise ValueError('容器注册来源映射不一致')
    remote = Path(origin['remote_manifest'])
    suffix = Path(original['run_id'])/'vllm-model-register'/remote.parent.name/remote.name
    if not remote.is_absolute() or '..' in remote.parts or Path(*remote.parts[-4:]) != suffix:
        raise ValueError('容器注册远端回执位置无效')
    if Path(receipt['path']) != attempt/'children/register'/suffix:
        raise ValueError('容器注册复制回执位置不一致')
    plan = sources.read(attempt/'register-plan.stdout.log')
    if plan.get('status') != 'not_run' or not isinstance(plan.get('request'), dict):
        raise ValueError('容器注册公开计划无效')
    return plan['request']


def review_handoff(value, raw_source):
    detail = value['detailed_report']
    if detail.get('source_sha256') != raw_source['sha256'] or value['evidence'].get('source') != raw_source['path']:
        raise ValueError('review 详细结果未绑定原始 verify 身份')
    if detail.get('review') != value['inputs'].get('cases'):
        raise ValueError('review 判断结果与请求不一致')
    original = sources.bound(raw_source)
    if original.get('operation') != 'verify' or value['evidence'].get('handoff') != original['evidence']['handoff']:
        raise ValueError('review 未沿用原始 verify 部署交接')
    return {'path':original['evidence']['handoff'], 'sha256':original['detailed_report']['handoff_sha256']}


def deployment_reference(value):
    if value.get('operation') == 'review':
        return review_handoff(value, value['inputs']['source'])
    if value.get('operation') != 'verify':
        raise ValueError('迁移缺少 verify/review 公开功能回执')
    return {'path':value['evidence']['handoff'], 'sha256':value['detailed_report']['handoff_sha256']}


def verify_responses(value):
    """复核原始响应与 HTTP/SSE 字节身份，不重新运行用例或判定语义。"""
    if value.get('operation') != 'verify':
        return
    rows = object_rows(value.get('detailed_report', {}).get('cases', []))
    for row in rows:
        if row.get('response'):
            response_digest(row['response'])
    if value['status'] != 'completed':
        return
    verify_coverage(value, rows)
    saved = json.loads(sources.stable_bytes(value['evidence']['cases']), object_pairs_hook=sources.pairs,
                       parse_constant=sources.invalid_constant)
    if saved != rows:
        raise ValueError('原始用例清单与验证报告不一致')


def object_rows(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('原始验证用例须为对象列表')
    return rows


def verify_coverage(value, rows):
    planned = object_rows(value['inputs']['cases'])
    expected = {row['id']:row['kind'] for row in planned if row.get('required') is True}
    actual = [row for row in rows if row.get('required') is True]
    if not expected or len(actual) != len(expected) or {row['id']:row['kind'] for row in actual} != expected:
        raise ValueError('原始验证必需用例覆盖与输入不一致')
    for row in actual:
        response_digest(row['response'])


def response_digest(response):
    for path, digest in (('path','sha256'), ('raw_path','raw_sha256')):
        if sources.ref(Path(response[path]))['sha256'] != response[digest]:
            raise ValueError('原始响应或 HTTP/SSE 证据内容已变化')
