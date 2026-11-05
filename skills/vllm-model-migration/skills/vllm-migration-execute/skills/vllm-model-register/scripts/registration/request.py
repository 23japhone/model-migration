"""显式注册请求和包外输出保护。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import re

from .evidence import read_json


def fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError(label + '字段缺失、未知或不是对象')
    return dict(value)


def text(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(label + '必须为非空字符串')
    return value


def identifier(value, label, dotted=False):
    pattern = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*' if dotted else r'[A-Za-z_][A-Za-z0-9_]*'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(label + '不是有效标识符')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('必须提供完整小写 SHA256')
    return value


def strings(value, label, allowed=None):
    if not isinstance(value, list):
        raise ValueError(label + '必须为列表')
    values = [text(v, label) for v in value]
    if len(values) != len(set(values)) or (allowed is not None and set(values) - allowed):
        raise ValueError(label + '存在重复或不支持的项')
    return values


def absolute(value, base, resolve=True):
    path = Path(text(value, '路径')).expanduser()
    path = base / path if not path.is_absolute() else path
    return str(path.resolve() if resolve else path.absolute())


def normalize_runtime(value, base):
    allowed = {'python', 'import_roots', 'plugins', 'timeout_seconds', 'context'}
    value = fields(value, allowed, allowed, 'runtime')
    value['python'] = absolute(value['python'], base, resolve=False)
    roots = strings(value['import_roots'], 'import_roots')
    if not roots:
        raise ValueError('import_roots 必须明确指定运行代码位置')
    value['import_roots'] = [absolute(v, base) for v in roots]
    plugins = strings(value['plugins'], 'plugins')
    if any(not re.fullmatch('[A-Za-z0-9_.-]+', p) for p in plugins):
        raise ValueError('plugin 名称无效')
    timeout = value['timeout_seconds']
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 300:
        raise ValueError('timeout_seconds 必须在 0.1 至 300 秒之间')
    if value['context'] not in ('development', 'deployment'):
        raise ValueError('context 必须为 development 或 deployment')
    return value


def normalize_plugin(value, base):
    keys = {'project_root', 'install_site', 'name', 'version', 'entrypoint'}
    value = fields(value, keys, keys, 'plugin')
    for key in ('project_root', 'install_site'):
        value[key] = absolute(value[key], base)
    for key in ('name', 'entrypoint'):
        identifier(value[key], key)
    if not isinstance(value['version'], str) or not re.fullmatch(r'\d+\.\d+\.\d+', value['version']):
        raise ValueError('plugin.version 必须为明确的三段版本')
    return value


def validate_resources():
    shared = release_skill('vllm-model-migration') / 'references/task-result.schema.json'
    try:
        schema, _ = read_json(shared)
        if schema['properties']['schema_version']['const'] != '1.0':
            raise ValueError('共同 Schema 版本不兼容')
        required = {'schema_version', 'task_id', 'run_id', 'inputs', 'status', 'conclusions', 'evidence', 'uncovered'}
        if not required.issubset(set(schema['required'])):
            raise ValueError('共同外层字段不完整')
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError('缺少或损坏共同 Schema 静态资源') from error


def load_request(path):
    validate_resources()
    base = Path(path).expanduser().resolve().parent
    raw, _ = read_json(path)
    keys = {'mode', 'allowed_changes', 'model', 'implementation', 'target', 'runtime', 'plugin', 'precheck'}
    request = fields(raw, keys, keys - {'plugin', 'precheck'}, '请求')
    if request['mode'] not in ('source', 'plugin'):
        raise ValueError('mode 必须为 source 或 plugin')
    request['allowed_changes'] = strings(request['allowed_changes'], 'allowed_changes', {'registry', 'plugin'})
    model = fields(request['model'], {'config', 'architecture'}, {'config', 'architecture'}, 'model')
    model['config'] = absolute(model['config'], base)
    identifier(model['architecture'], 'architecture')
    request['model'] = model
    implementation = fields(request['implementation'], {'module', 'class_name', 'path', 'sha256'},
                            {'module', 'class_name', 'path', 'sha256'}, 'implementation')
    identifier(implementation['module'], 'module', dotted=True)
    identifier(implementation['class_name'], 'class_name')
    implementation['path'] = absolute(implementation['path'], base)
    sha(implementation['sha256'])
    request['implementation'] = implementation
    target = fields(request['target'], {'vllm', 'vllm_ascend', 'registry_path', 'registry_sha256'},
                    {'vllm', 'registry_path', 'registry_sha256'}, 'target')
    text(target['vllm'], 'target.vllm')
    if 'vllm_ascend' in target:
        text(target['vllm_ascend'], 'target.vllm_ascend')
    target['registry_path'] = absolute(target['registry_path'], base)
    sha(target['registry_sha256'])
    request['target'] = target
    request['runtime'] = normalize_runtime(request['runtime'], base)
    if request['mode'] == 'plugin':
        request['plugin'] = normalize_plugin(request.get('plugin'), base)
    elif 'plugin' in request:
        raise ValueError('source 模式不接受 plugin 效果参数')
    if 'precheck' in request:
        item = fields(request['precheck'], {'manifest', 'sha256'}, {'manifest', 'sha256'}, 'precheck')
        item['manifest'] = absolute(item['manifest'], base)
        sha(item['sha256'])
        request['precheck'] = item
    return request


def protected_paths(request):
    installed = release_root()
    registry = Path(request['target']['registry_path'])
    if tuple(registry.parts[-4:]) != ('vllm', 'model_executor', 'models', 'registry.py'):
        raise ValueError('registry_path 必须指向目标 vLLM 标准注册入口')
    return [Path(request['model']['config']).parent, registry.parents[3],
            Path(request['implementation']['path']).parent, installed]


def output_target(request, work_root, run_id):
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id) or run_id in ('.', '..'):
        raise ValueError('run_id 必须为安全的单层标识')
    root = Path(work_root).expanduser().resolve()
    target = root / run_id / 'vllm-model-register'
    for path in protected_paths(request):
        if root.is_relative_to(path) or target.is_relative_to(path) or path.is_relative_to(target):
            raise ValueError('运行证据必须位于模型、源码和安装资源之外')
    if target.resolve() != target:
        raise ValueError('运行目录不能经符号链接重定向')
    if request['mode'] == 'plugin':
        protect_plugin_paths(request, target)
    return target


def protect_plugin_paths(request, target):
    plugin = request['plugin']
    project, site = (Path(plugin[k]) for k in ('project_root', 'install_site'))
    protected = [*protected_paths(request), target, Path(request['runtime']['python']).parent.parent]
    for output in (project, site):
        if any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
            raise ValueError('plugin 项目与安装 site 必须使用隔离包外目录')
    if site.is_relative_to(project) or project.is_relative_to(site):
        raise ValueError('plugin 项目与安装 site 不能重叠')
    if str(site) not in request['runtime']['import_roots']:
        raise ValueError('隔离 install_site 必须明确进入 import_roots')
