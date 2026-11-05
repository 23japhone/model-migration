"""依据准备回执绑定原生数据路径；同一逻辑用于预检和正式冻结。"""
import copy
import importlib.metadata
import os
from pathlib import Path

from ...evidence import sha256, write
from ...inputs import dataset
from .configuration import load_config, native_config, validate
from .samples import verify


def bind_config(raw, asset):
    result = copy.deepcopy(raw)
    root = Path(asset['path'])
    for benchmark, args in result['dataset_args'].items():
        if 'local_path' not in args:
            args['local_path'] = str(root)
        value = args['local_path']
        if not isinstance(value, str) or not value:
            raise ValueError('dataset_path_conflict: ' + benchmark + ' 的显式 local_path 必须为非空路径')
        path = Path(value)
        if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root):
            raise ValueError(f'dataset_path_conflict: {benchmark} 配置路径={value}；验收路径={root}；'
                             '请删除 local_path 由回执绑定，或填写验收根内的原生数据路径；原输入未修改')
    return result


def metadata_ready(benchmark, root):
    if benchmark == 'ceval' and not (Path(root) / 'README.md').is_file():
        raise ValueError(f'dataset_metadata_missing: ceval 的 {root}/README.md 缺失；'
                         '请以 layout=evalscope 重新准备数据并引用新回执')


def native_task(raw, task, asset, attempt):
    from evalscope.api.registry import get_benchmark
    single = bind_config(raw, asset)
    name = task['benchmark']
    single['datasets'] = [name]
    single['dataset_args'] = {name: single['dataset_args'][name]}
    single['dataset_args'][name]['subset_list'] = [task['subset']]
    single.update(work_dir=str(attempt / 'tasks' / task['dataset'] / 'evalscope'), no_timestamp=True,
                  dataset_dir=str(attempt / 'dataset-cache'))
    metadata_ready(name, single['dataset_args'][name]['local_path'])
    config = native_config(single)
    adapter = get_benchmark(name, config)
    return config, adapter, verify(adapter, asset, task)


def diagnostic(error, task=None):
    message = str(error)
    code = message.split(':', 1)[0] if message.startswith('dataset_') else 'dataset_preflight_failed'
    return {'code': code, 'type': type(error).__name__, 'message': message,
            'dataset': (task or {}).get('dataset'), 'subset': (task or {}).get('subset')}


def offline_environment(evaluator, attempt):
    from ..versioning import inspect_environment
    installed = importlib.metadata.version('evalscope')
    expected = evaluator.get('version', 'latest')
    checked = inspect_environment('evalscope', {**evaluator, 'version': installed if expected == 'latest' else expected}, attempt)
    return {**checked, 'requested_version': expected, 'latest_checked': False}


def inspect(payload, output):
    # 本操作仅原生本地加载；缓存也位于尝试目录，不落入数据根。
    attempt = Path(payload['attempt'])
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      HF_DATASETS_CACHE=str(attempt / 'dataset-cache'))
    request = payload['request']; result = {'status': 'blocked', 'tasks': [], 'errors': []}
    try:
        result['environment'] = offline_environment(request['evaluator'], attempt)
        source = request['evaluator']['config']
        if sha256(source['path']) != source['sha256']:
            raise ValueError('dataset_config_changed: 评测配置摘要已变化')
        raw = load_config(source['path']); validate(raw, request['tasks'], payload['handoff'])
        asset = dataset(request['dataset'])
        for task in request['tasks']:
            inspect_task(raw, task, asset, attempt, result)
        if asset != dataset(request['dataset']) or sha256(source['path']) != source['sha256']:
            raise ValueError('dataset_identity_changed: 预检期间数据或配置发生变化')
        result['status'] = 'blocked' if result['errors'] else 'completed'
        result.update(dataset=request['dataset'], config=source)
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as error:
        result['errors'].append(diagnostic(error))
        result['status'] = 'blocked'
    write(output, result)


def inspect_task(raw, task, asset, attempt, result):
    try:
        config, _, selection = native_task(raw, task, asset, attempt)
        resolved = config.model_dump(mode='json')
        path = attempt / ('preflight-' + task['dataset'] + '.json'); write(path, resolved)
        result['tasks'].append({'dataset': task['dataset'], 'subset': task['subset'],
                                'count': len(selection['samples']),
                                'reader_samples_sha256': selection['reader_samples_sha256'],
                                'config': {'path': str(path), 'sha256': sha256(path)}})
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        result['errors'].append(diagnostic(error, task))
