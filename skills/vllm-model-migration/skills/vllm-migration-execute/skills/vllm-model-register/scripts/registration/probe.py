"""独立目标进程检查；只 import，不实例化模型或执行推理。"""
import importlib
import importlib.util
from importlib.machinery import SourceFileLoader
from importlib import metadata
import inspect
import os
from pathlib import Path
import platform
import sys

from .evidence import digest, read_bytes, read_json, write_json
from .plugin import callback_source


def mapping(entry):
    if entry is None:
        return None
    module = getattr(entry, 'module_name', None)
    name = getattr(entry, 'class_name', None)
    if isinstance(module, str) and isinstance(name, str):
        return {'module': module, 'class_name': name, 'kind': 'lazy'}
    cls = getattr(entry, 'cls', None)
    if isinstance(cls, type):
        return {'module': cls.__module__, 'class_name': cls.__name__, 'kind': 'class'}
    raise ValueError('目标运行注册条目不是可识别的 lazy/class 形式')


def check_sdk(request):
    import vllm
    target = request['target']
    version = vllm.__version__
    if version.split('+', 1)[0] != target['vllm']:
        raise ValueError('实际 vLLM 版本不符合目标')
    module = importlib.import_module('vllm.model_executor.models.registry')
    path = Path(module.__file__).resolve()
    raw = read_bytes(path)
    if path != Path(target['registry_path']) or digest(raw) != target['registry_sha256']:
        raise ValueError('实际载体的 registry 路径或 SHA256 与本次代码不一致')
    result = {'version': version, 'vllm_path': str(Path(vllm.__file__).resolve()),
              'vllm_init_sha256': digest(read_bytes(vllm.__file__)), 'registry_path': str(path),
              'registry_sha256': digest(raw), 'platform': platform.system() + '/' + platform.machine(),
              'runtime_environment': {k: os.environ[k] for k in ('ASCEND_RT_VISIBLE_DEVICES', 'TRITON_ASCEND_ARCH')
                                      if k in os.environ}}
    if 'vllm_ascend' in target:
        installed = metadata.version('vllm-ascend')
        if installed.split('+', 1)[0] != target['vllm_ascend']:
            raise ValueError('实际 vLLM-Ascend 版本不符合目标')
        result['vllm_ascend_version'] = installed
    return vllm.ModelRegistry, result


def entrypoint_candidate(request, phase):
    plugin = request['plugin']
    entries = [entry for entry in metadata.entry_points(group='vllm.general_plugins')
               if entry.name == plugin['entrypoint']]
    if not entries and phase == 'before':
        if importlib.util.find_spec(plugin['name']) is not None:
            raise ValueError('plugin module 名称已被既有代码占用，且缺少本次 entry point 身份')
        return None
    if len(entries) != 1:
        raise ValueError('plugin entry point 缺失或存在同名冲突')
    return entries[0]


def check_entrypoint(request, phase):
    if request['mode'] != 'plugin':
        return None
    plugin = request['plugin']
    entry = entrypoint_candidate(request, phase)
    if entry is None:
        return None
    distribution = entry.dist.metadata['Name'].lower().replace('-', '_')
    if distribution != plugin['name'].lower().replace('-', '_'):
        raise ValueError('plugin distribution 身份与本次产物不一致')
    if entry.value != plugin['name'] + ':register' or entry.dist.version != plugin['version']:
        raise ValueError('plugin entry point 内容或版本与本次产物不一致')
    path = Path(entry.dist.locate_file(plugin['name'] + '/__init__.py')).resolve()
    expected_path = Path(plugin['install_site']) / plugin['name'] / '__init__.py'
    if path != expected_path:
        raise ValueError('plugin 的实际安装路径不属于请求的隔离 site')
    if digest(read_bytes(path)) != digest(callback_source(request)):
        raise ValueError('已安装 plugin callback 身份不一致，不能加载未知代码')
    return {'name': entry.name, 'distribution': entry.dist.metadata['Name'], 'value': entry.value,
            'version': entry.dist.version, 'path': str(path),
            'sha256': digest(read_bytes(path))}


def callback_origin(request, plugin):
    spec = importlib.util.find_spec(request['plugin']['name'])
    if spec is None or type(spec.loader) is not SourceFileLoader:
        raise ValueError('实际 callback 解析不是已核对的源码 loader')
    path = Path(spec.origin).resolve()
    if path != Path(plugin['path']) or digest(read_bytes(path)) != plugin['sha256']:
        raise ValueError('实际 callback 解析路径或 SHA256 不一致，拒绝执行同名遮蔽代码')
    return path


def loaded_callback(request, plugin):
    expected = callback_origin(request, plugin)
    module = importlib.import_module(request['plugin']['name'])
    path = Path(module.__file__).resolve()
    callback = getattr(module, 'register', None)
    if not inspect.isfunction(callback):
        raise ValueError('已加载 plugin 缺少已核对的 register 函数')
    callback_path = Path(inspect.getfile(callback)).resolve()
    if path != expected or callback_path != expected or digest(read_bytes(path)) != plugin['sha256']:
        raise ValueError('已加载 callback 的实际模块、函数路径或 SHA256 不一致')
    plugin.update(resolved_path=str(expected), actual_module_path=str(path),
                  actual_callback_path=str(callback_path), actual_sha256=digest(read_bytes(path)))
    return module


def import_class(request, registry, phase):
    implementation = request['implementation']
    entry = registry.models.get(request['model']['architecture'])
    observed = mapping(entry)
    expected = {k: implementation[k] for k in ('module', 'class_name')}
    if phase == 'after' and (observed is None or any(observed[k] != v for k, v in expected.items())):
        raise ValueError('实际 architecture 映射缺失或与目标实现冲突')
    if observed and all(observed[k] == v for k, v in expected.items()):
        cls = entry.load_model_cls()
    else:
        cls = getattr(importlib.import_module(implementation['module']), implementation['class_name'])
    if not isinstance(cls, type) or cls.__name__ != implementation['class_name']:
        raise ValueError('实际 import 不是指定 model class')
    path = Path(inspect.getfile(cls)).resolve()
    sha = digest(read_bytes(path))
    if path != Path(implementation['path']) or sha != implementation['sha256']:
        raise ValueError('实际载体的 class 源码路径或 SHA256 不一致')
    return {'module': cls.__module__, 'class_name': cls.__name__, 'path': str(path), 'sha256': sha}, observed


def inspect_registration(request, phase, result):
    registry, sdk = check_sdk(request)
    result['sdk'] = sdk
    before = mapping(registry.models.get(request['model']['architecture']))
    result['mapping_before_plugins'] = before
    plugin = check_entrypoint(request, phase)
    result['plugin'] = plugin
    if plugin is not None:
        # 仅 import 已验证的生成模块；register() 仍由真实框架 loader 调用。
        loaded_callback(request, plugin)
    from vllm.plugins import load_general_plugins
    load_general_plugins()
    if plugin is not None:
        module = loaded_callback(request, plugin)
        state = getattr(module, 'REGISTRATION_STATE', None)
        loaded = isinstance(state, dict) and state.get('architecture') == request['model']['architecture']
        plugin.update(loaded=loaded, state=state)
        result['plugin'] = plugin
        if not loaded:
            raise ValueError('plugin 已发现但未在该独立进程加载，不能视为注册通过')
    cls, after = import_class(request, registry, phase)
    result.update(mapping=after, class_import=cls, plugins_requested=request['runtime']['plugins'])


def main():
    payload, _ = read_json(sys.argv[1])
    result = {'probe_version': '1.0', 'nonce': payload['nonce'], 'phase': payload['phase'],
              'pid': os.getpid(), 'python': sys.executable, 'python_version': platform.python_version(),
              'context': payload['request']['runtime']['context'], 'ok': False, 'errors': []}
    try:
        inspect_registration(payload['request'], payload['phase'], result)
        result['ok'] = True
    except (OSError, ValueError, TypeError, KeyError, ImportError, AttributeError, RuntimeError, SystemError) as error:
        result['errors'].append(str(error))
    write_json(sys.argv[2], result)
    return 0 if result['ok'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
