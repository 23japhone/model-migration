"""完整资产、准备/适配交接和构建输入身份。"""
import json
from pathlib import Path

from .evidence import read_bound, sha256, write_json


def file_inventory(root, expected, model_cache=False):
    root = Path(root).resolve()
    result = {'files': {}, 'errors': []}
    for name, digest in expected.items():
        path = root / name
        try:
            check_inventory_path(root, name, model_cache)
            observed = sha256(path)
            result['files'][name] = {'sha256': observed, 'bytes': path.stat().st_size, 'match': observed == digest}
            if observed != digest:raise ValueError('SHA256 不一致')
        except (OSError, ValueError) as error:
            result['errors'].append(name + '：' + str(error))
    return result


def check_inventory_path(root, name, allow_cache):
    if allow_cache:
        from model_cache import checked
        checked(root, name)
        return
    path = root / name
    if any(p.is_symlink() for p in [path, *path.parents]) or not path.resolve().is_relative_to(root):
        raise ValueError('文件越界或使用符号链接')


def assets(model):
    root = Path(model['root'])
    result = file_inventory(root, model['files'], model_cache=True)
    actual = {str(p.relative_to(root)) for p in root.rglob('*')
              if p.suffix in ('.safetensors', '.bin', '.pt', '.pth')}
    if actual != set(model['weight_files']):
        result['errors'].append('完整权重文件集合与显式清单不一致')
    try:
        config = json.loads((root / model['config']).read_text(encoding='utf-8'))
        if not config.get('architectures'):raise ValueError('原始配置缺少 architectures')
        result['architectures'] = config['architectures']
        for index in root.glob('*.index.json'):
            relative = str(index.relative_to(root))
            if relative not in model['files']:raise ValueError('权重 index 未固定身份')
            mapping = json.loads(index.read_text(encoding='utf-8'))['weight_map']
            if set(mapping.values()) - set(model['weight_files']):raise ValueError('index 引用未列入清单的 shard')
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['errors'].append('config/index：' + str(error))
    return result


HANDOFF_TASKS = {'prepare': 'vllm-migration-prepare', 'model': 'vllm-model-prepare',
                 'image': 'vllm-image-prepare', 'precheck': 'vllm-model-precheck',
                 'adaptation': 'adapt-agent', 'registration': 'vllm-model-register'}


def receipt_outer(receipt, name):
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != '1.0'
            or receipt.get('status') != 'completed' or name not in HANDOFF_TASKS
            or receipt.get('task_id') != HANDOFF_TASKS[name]):
        raise ValueError('交接版本、任务身份或终态无效')
    receipt_fields(receipt)


def receipt_fields(receipt):
    if not isinstance(receipt.get('run_id'), str) or not receipt['run_id'].strip():
        raise ValueError('交接缺少有效run_id')
    for field in ['inputs', 'evidence', 'conclusions']:
        if not isinstance(receipt.get(field), dict):raise ValueError('共同外层对象字段缺失')
    if not isinstance(receipt.get('uncovered'), list) or any(not isinstance(v, str) for v in receipt['uncovered']):
        raise ValueError('未覆盖项字段无效')
    values = receipt['conclusions']
    if not values or any(v not in ('pass', 'fail', 'not_evaluated', 'review_required') for v in values.values()):
        raise ValueError('共同结论字段无效')
    if any(not isinstance(v, str) for v in receipt['evidence'].values()):raise ValueError('共同证据字段无效')


def precheck_identity(receipt, request):
    details = receipt.get('details', {})
    if details.get('route') not in ('supported_candidate', 'registration_required', 'adaptation_required'):
        raise ValueError('预检路线不允许进入部署')
    expected = request['model']['files'][request['model']['config']]
    if details.get('model', {}).get('config_sha256') != expected:
        raise ValueError('预检与实际原始模型配置身份不一致')
    if receipt['conclusions'].get('version_materials') != 'pass':raise ValueError('预检版本资料未通过')
    target = receipt['inputs'].get('target', {})
    wanted = target.get('vllm')
    actual = request['runtime']['versions'].get('vllm')
    if not isinstance(wanted, str) or not wanted or not isinstance(actual, str):
        raise ValueError('预检目标与运行vLLM版本必须为非空字符串')
    # 与迁移阶段一致：显式 local 版本固定完整构建，否则仅比较公开版本。
    compared = actual if '+' in wanted else actual.split('+', 1)[0]
    if wanted != compared:raise ValueError('预检目标与运行vLLM版本不一致')


def collect(request, attempt):
    result = {'model': assets(request['model']), 'handoffs': {}, 'errors': []}
    result['errors'].extend(result['model']['errors'])
    for name, item in request.get('handoffs', {}).items():
        try:
            receipt = read_bound(item)
            receipt_outer(receipt, name)
            ready_handoff(receipt,name,request)
            result['handoffs'][name] = receipt
        except (OSError, ValueError, TypeError, KeyError) as error:
            result['errors'].append(name + '：' + str(error))
    if 'build' in request:
        build = request['build']
        checked = file_inventory(build['context'], build['files'])
        actual = {str(p.relative_to(build['context'])) for p in Path(build['context']).rglob('*') if p.is_file()}
        if actual != set(build['files']):checked['errors'].append('构建 context 必须完整固定全部文件')
        if build['dockerfile'] not in build['files']:checked['errors'].append('Dockerfile 未固定身份')
        result['build'] = checked
        result['errors'].extend(checked['errors'])
    write_json(attempt / 'inputs.json', result)
    return result


def ready_handoff(receipt,name,request):
    values = dict(receipt['conclusions'])
    if name=='precheck':
        precheck_identity(receipt,request)
        required = {'registration_required':{'registration'},'adaptation_required':{'registration','adaptation'}}.get(receipt['details']['route'],set())
        if required-request.get('handoffs',{}).keys():raise ValueError('预检路线所需注册/适配交接缺失')
        if required and values.get('support')=='review_required':values.pop('support')
    expected = {'registration':'registration','adaptation':'adaptation'}.get(name)
    if expected and values.get(expected)!='pass':raise ValueError('前置任务未完成对应必需检查')
    if any(v in ('fail','review_required') for v in values.values()):
        raise ValueError('前置任务含失败或待复核结论，不能作为就绪交接')
