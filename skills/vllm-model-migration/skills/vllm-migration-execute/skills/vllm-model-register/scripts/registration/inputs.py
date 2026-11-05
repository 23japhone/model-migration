"""注册前的原始配置、实现、源码和可选预检回执绑定。"""
import ast
from pathlib import Path

from .evidence import digest, read_bytes, read_json


def model_input(request):
    model = request['model']
    config, sha = read_json(model['config'])
    architectures = config.get('architectures')
    if not isinstance(architectures, list) or any(not isinstance(v, str) for v in architectures):
        raise ValueError('原始 config 缺少有效 architectures')
    if model['architecture'] not in architectures:
        raise ValueError('请求 architecture 不在原始 config 中，不能改名掩盖不兼容')
    return {'config_path': model['config'], 'config_sha256': sha, 'architectures': architectures}


def source_module_path(root, module):
    parts = module.split('.')
    if any(not part.isidentifier() for part in parts):
        raise ValueError('源码注册的 module 标识无效')
    stem = root.joinpath(*parts)
    candidates = [path for path in (stem.with_suffix('.py'), stem / '__init__.py') if path.is_file()]
    if len(candidates) != 1:
        raise ValueError('源码 module 必须唯一对应已有模块文件或包入口')
    path = candidates[0]
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('源码 module 路径为符号链接')
    if not path.resolve().is_relative_to(root):
        raise ValueError('源码 module 路径越界或为符号链接')
    return path.resolve()


def implementation_input(request):
    item = request['implementation']
    raw = read_bytes(item['path'])
    if digest(raw) != item['sha256']:
        raise ValueError('已有实现 SHA256 不一致')
    tree = ast.parse(raw.decode('utf-8-sig'))
    if not any(isinstance(v, ast.ClassDef) and v.name == item['class_name'] for v in tree.body):
        raise ValueError('目标文件未直接声明指定 class')
    if request['mode'] == 'source':
        prefix = 'vllm.model_executor.models.'
        module = item['module']
        if not module.startswith(prefix):
            raise ValueError('源码注册只能使用 models 下的已有模块')
        root = Path(request['target']['registry_path']).parent
        expected = source_module_path(root, module[len(prefix):])
        if Path(item['path']) != expected:
            raise ValueError('源码 module 与实际实现路径不一致')
    return dict(item)


def registry_input(request):
    item = request['target']
    raw = read_bytes(item['registry_path'])
    if digest(raw) != item['registry_sha256']:
        raise ValueError('目标 registry SHA256 不一致，不能修改未知源码')
    ast.parse(raw.decode('utf-8-sig'))
    return {'path': item['registry_path'], 'sha256': digest(raw)}


def receipt_details(value, sha, item):
    valid = sha == item['sha256'] and value.get('schema_version') == '1.0'
    valid &= value.get('task_id') == 'vllm-model-precheck' and value.get('status') == 'completed'
    if not valid:
        raise ValueError('预检回执身份、摘要或终态无效')
    types = {'inputs': dict, 'evidence': dict, 'uncovered': list, 'conclusions': dict}
    if not isinstance(value.get('run_id'), str) or not value['run_id'].strip():
        raise ValueError('预检回执共同外层缺少有效 run_id')
    if any(not isinstance(value.get(key), kind) for key, kind in types.items()):
        raise ValueError('预检回执共同外层字段缺失或类型无效')
    if (not value['conclusions'] or any(v not in ('pass', 'fail', 'not_evaluated', 'review_required')
                                      for v in value['conclusions'].values())
            or any(not isinstance(v, str) for v in [*value['evidence'].values(), *value['uncovered']])):
        raise ValueError('预检回执共同外层结论或证据类型无效')
    details = value.get('details')
    if not isinstance(details, dict):
        raise ValueError('预检回执 details 不是对象')
    return details


def precheck_input(request, model):
    if 'precheck' not in request:
        return {'provided': False, 'note': '独立调用；不冒充已有预检交接'}
    item = request['precheck']
    value, sha = read_json(item['manifest'])
    details = receipt_details(value, sha, item)
    if details.get('route') not in ('supported_candidate', 'registration_required', 'adaptation_required'):
        raise ValueError('预检路线未允许进入注册')
    if details.get('selected_architecture') != request['model']['architecture']:
        raise ValueError('预检选择的架构与注册请求不一致')
    if value['conclusions'].get('version_materials') != 'pass':
        raise ValueError('预检版本资料未通过')
    target = value['inputs'].get('target')
    versions = {k: v for k, v in request['target'].items() if k in ('vllm', 'vllm_ascend')}
    if not isinstance(target, dict) or any(target.get(k) != v for k, v in versions.items()):
        raise ValueError('预检与注册的目标版本不一致')
    checked = details.get('model')
    if not isinstance(checked, dict) or checked.get('config_sha256') != model.get('config_sha256'):
        raise ValueError('预检 config 身份与本次原始模型不一致')
    return {'provided': True, 'manifest': item['manifest'], 'sha256': sha, 'route': details['route']}


def collect_inputs(request):
    result = {'errors': []}
    collectors = {'model': model_input, 'implementation': implementation_input, 'registry': registry_input}
    for owner, collect in collectors.items():
        try:
            result[owner] = collect(request)
        except (OSError, ValueError, TypeError, KeyError, SyntaxError) as error:
            result[owner] = {'error': str(error)}
            result['errors'].append(owner + '：' + str(error))
    try:
        result['precheck'] = precheck_input(request, result['model'])
    except (OSError, ValueError, TypeError, KeyError) as error:
        result['precheck'] = {'error': str(error)}
        result['errors'].append('precheck：' + str(error))
    return result
