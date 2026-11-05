"""工具身份与双适配器分派；不在 CLI 进程导入评测 SDK。"""
import importlib
from pathlib import Path
import re

BACKENDS = {'evalscope', 'aisbench'}
NATIVE_FORMATS = {'evalscope-native-v1': 'evalscope', 'aisbench-native-v1': 'aisbench'}


class PreparationBlocked(ValueError):
    """必需评测环境或配置未就绪，尚未发送推理请求。"""


def resolve(evaluator):
    backend = evaluator.get('backend')
    reason = 'explicit'
    if backend is None:
        legacy = Path(evaluator.get('config', {}).get('path', '')).suffix == '.py'
        backend, reason = ('aisbench', 'legacy') if legacy else ('evalscope', 'default')
    if backend not in BACKENDS:
        raise ValueError('评测工具须为 evalscope 或 aisbench')
    version = evaluator.get('version', 'latest')
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}', version):
        raise ValueError('version 须省略、为 latest 或旧请求的版本字符串')
    return backend, reason


def adapter(request):
    backend, _ = resolve(request['evaluator'])
    module = '.runtime.aisbench' if backend == 'aisbench' else '.runtime.evalscope.adapter'
    return importlib.import_module(module, __package__)


def read_task(root, task):
    backend = task.get('backend', 'aisbench')
    if backend not in BACKENDS:
        raise ValueError('历史任务工具身份无效')
    module = '.results' if backend == 'aisbench' else '.runtime.evalscope.results'
    return importlib.import_module(module, __package__).read_task(root, task)


def bind(prepared, request):
    backend, reason = resolve(request['evaluator'])
    conditions = prepared['conditions']
    if not isinstance(conditions.get('evaluator_version'), str) or not conditions['evaluator_version']:
        raise ValueError('实际配置探测缺少工具版本')
    version_identity(conditions, backend)
    conditions.update(evaluator_backend=backend, evaluator_resolution=reason)
    for task in prepared['tasks']:
        task['backend'] = backend
    return prepared


def comparable_conditions(conditions):
    if conditions is None:
        return None
    result = dict(conditions)
    result.pop('evaluator_resolution', None)
    if 'evaluator_backend' not in result and result.get('evaluator_version'):
        result['evaluator_backend'] = 'aisbench'
    return result


def source_backend(conditions, format_name=None):
    if not isinstance(conditions, dict):
        raise ValueError('历史评测条件必须为对象')
    backend = conditions.get('evaluator_backend')
    if backend is None and conditions.get('evaluator_version'):
        backend = 'aisbench'
    if backend not in BACKENDS or not isinstance(conditions.get('evaluator_version'), str) or not conditions['evaluator_version']:
        raise ValueError('历史产物缺少实际工具或版本身份')
    version_identity(conditions, backend)
    if format_name is not None and NATIVE_FORMATS.get(format_name) != backend:
        raise ValueError('原生产物格式与工具身份不一致')
    return backend


def version_identity(conditions, backend):
    version = conditions['evaluator_version']
    if not re.fullmatch(r'v?\d[A-Za-z0-9.!+_-]*', version):
        raise ValueError('产物必须记录实际版本，不能以 latest 或其他标签代替')
    environment = conditions.get('evaluator_identity')
    if environment is not None:
        package = 'evalscope' if backend == 'evalscope' else 'ais-bench-benchmark'
        packages = environment.get('packages', {}) if isinstance(environment, dict) else None
        if not isinstance(packages, dict) or (package in packages and packages[package] != version):
            raise ValueError('实际工具版本与保存的评测环境依赖身份不一致')


def history_identity(details):
    conditions = details['conditions']
    if not isinstance(conditions, dict):
        raise ValueError('历史评测条件必须为对象')
    if not conditions.get('evaluator_backend') and not any(t.get('backend') for t in details['tasks']):
        if 'evaluator_version' in conditions:
            source_backend(conditions)
        return  # 旧 AISBench 报告保留既有只读分析入口。
    backend = source_backend(conditions)
    if any(task.get('backend', 'aisbench') != backend for task in details['tasks']):
        raise ValueError('历史任务和条件的工具身份不一致')
    if not protocol_complete(conditions):
        raise ValueError('历史 EvalScope 报告缺少 benchmark 评测版本')


def protocol_complete(conditions):
    if conditions.get('evaluator_backend') != 'evalscope':
        return True
    keys = ('benchmark', 'subset', 'evaluation_version')
    datasets = conditions.get('datasets')
    return isinstance(datasets, list) and bool(datasets) and all(
        isinstance(row, dict) and all(isinstance(row.get(key), str) and row[key] for key in keys)
        for row in datasets)
