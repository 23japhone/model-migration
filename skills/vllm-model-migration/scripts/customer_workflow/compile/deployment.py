"""由准备回执和固定版本配方生成部署请求，不直接操作模型服务。"""
import copy
from pathlib import PurePosixPath

from coordination.runtime import sources
from service_address import base_url
from .generation import cases
from allocation_contract import MEMORY_OVERRIDES, POLICY

HOST = '127.0.0.1'
START_PORT = 8000


def allocation_request(task, observed, spec):
    """从本次主机观察筛选允许设备，不把客户范围当作固定 TP。"""
    pool = set(task['environment']['available_npu_devices'])
    observed_ids = {row['id'] for row in observed['devices']}
    if pool - observed_ids:
        raise ValueError('可用设备范围缺少实际观察：' + ','.join(map(str, sorted(pool - observed_ids))))
    return {'operation': 'allocate', 'spec': spec, 'requested': -1, 'policy': POLICY,
            'devices': [row for row in observed['devices'] if row['id'] in pool]}


def check_allocation(task, allocation):
    devices = allocation['devices']
    pool = set(task['environment']['available_npu_devices'])
    if (not devices or any(type(item) is not int for item in devices)
            or len(set(devices)) != len(devices) or not set(devices) <= pool
            or len(devices) != allocation['tensor_parallel_size']):
        raise ValueError('分配结果超出客户设备范围或 TP 与设备不一致')


def prepared_model(preparation):
    row = preparation['tasks']['model']
    ref = row.get('receipt') or {'path': row['manifest'], 'sha256': row['manifest_sha256']}
    asset = sources.bound(ref)['asset']
    return asset


def precheck(asset, observations):
    recipe = observations['deployment']
    config = asset['metadata']['config']
    optional = recipe.get('precheck_optional', {})
    if optional.keys() - {'implementation', 'adaptation'}:
        raise ValueError('预检扩展不得覆盖模型、版本或来源身份')
    return {'model': {'path': asset['path'], 'task': 'chat', 'modalities': ['text'],
                      'dtype': recipe['dtype'], 'quantization': (config.get('quantization_config') or {}).get('quant_method')},
            'target': observations['target'], 'sources': source_projection(recipe['sources'])['precheck'], 'reviews': recipe['reviews'],
            'features': ['eager'], **optional}


def source_projection(observed):
    """镜像观察拆为静态预检来源与容器路径；不改写原观察或运行文件清单。"""
    if not isinstance(observed, dict) or set(observed) != {'vllm', 'vllm_ascend'}:
        raise ValueError('sources 须包含 vllm 和 vllm_ascend')
    allowed = {'path', 'revision', 'expected_identity', 'kind', 'distribution', 'version', 'image_id', 'runtime_path'}
    projected, paths = {}, {}
    for name, source in observed.items():
        label = 'sources.' + name
        if not isinstance(source, dict):
            raise ValueError(label + ' 须为对象')
        unknown = set(source) - allowed
        if unknown:
            raise ValueError(label + ' 未知字段：' + ', '.join(sorted(unknown)))
        projected[name] = copy.deepcopy(source)
        if 'runtime_path' in source:
            paths[name] = runtime_source_path(source, label)
            projected[name].pop('runtime_path')
    return {'precheck': projected, 'runtime_paths': paths}


def runtime_source_path(source, label):
    path = source['runtime_path']
    if source.get('kind') != 'installed_package':
        raise ValueError(label + '.runtime_path 仅适用于 installed_package 观察')
    if not isinstance(path, str) or not path.strip() or '\0' in path:
        raise ValueError(label + '.runtime_path 须为非空路径')
    if not PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
        raise ValueError(label + '.runtime_path 须为容器内绝对路径且不能包含 ..')
    return path


def extensions(recipe):
    value = recipe.get('migration_extensions', {})
    if value.keys() - {'register', 'register_container', 'adaptation', 'adaptation_roots', 'timeout_seconds'}:
        raise ValueError('迁移扩展不得覆盖客户流程生成的准备、预检或部署请求')
    return value


def command(task, asset, recipe, allocation, port, context):
    check_allocation(task, allocation)
    options = copy.deepcopy(recipe.get('server_options', {}))
    reserved = {'model', 'served_model_name', 'host', 'port', 'tensor_parallel_size', 'max_model_len',
                'gpu_memory_utilization', 'dtype', 'enforce_eager', 'max_num_seqs'}
    if (reserved | MEMORY_OVERRIDES) & options.keys():
        raise ValueError('版本配方不得覆盖客户字段或资源规划参数')
    options.update(model=recipe['runtime_model_path'], served_model_name=task['model']['model_name'],
                   host=HOST, port=port, dtype=recipe['dtype'], enforce_eager=True,
                   tensor_parallel_size=allocation['tensor_parallel_size'], max_model_len=context,
                   gpu_memory_utilization=allocation['memory_fraction'], max_num_seqs=recipe['max_num_seqs'])
    # 与交付生成使用同一参数编码，避免包装命令造成无损导出失败。
    from tuning_handoff.deployment import encoded
    argv = [recipe['runtime']['python'], '-m', 'vllm.entrypoints.openai.api_server']
    for key, value in options.items():
        argv.extend(['--' + key.replace('_', '-'), *encoded(value)])
    return argv


def verify(task, asset, preparation, observations, allocation, port, context, functional):
    recipe = observations['deployment']
    if set(recipe['container_resources']) != {'memory', 'shm_size', 'cpus'}:
        raise ValueError('容器资源配方只能指定 memory/shm_size/cpus，不能覆盖容器身份')
    if context > recipe['model_max_context']:
        raise ValueError('模型上下文不足以容纳真实精度输入及 8192 输出预算')
    runtime = copy.deepcopy(recipe['runtime'])
    runtime['kind'] = 'docker'
    runtime['env'].pop('ASCEND_RT_VISIBLE_DEVICES', None)
    runtime['env']['ASCEND_VISIBLE_DEVICES'] = ','.join(map(str, allocation['devices']))
    image_row = preparation['tasks']['image']
    image = image_row.get('asset')
    if image is None:
        image = sources.bound(image_row['receipt'])['asset']
    mounted = [{'source': asset['path'], 'target': recipe['runtime_model_path'], 'read_only': True},
               *recipe['mounts']]
    from model_cache import repository_root
    cache = repository_root(asset['path'])
    if cache is not None:
        # 同路径挂载完整单仓库，快照中的 ../../blobs 链接在容器内仍可解析。
        recipe = copy.deepcopy(recipe)
        recipe['runtime_model_path'] = asset['path']
        mounted[0] = {'source': str(cache), 'target': str(cache), 'read_only': True}
    devices = ['/dev/davinci' + str(n) for n in allocation['devices']] + recipe['control_devices']
    model = {'name': task['model']['model_name'], 'root': asset['path'],
             'runtime_root': recipe['runtime_model_path'],
             'files': {row['path']: row['sha256'] for row in asset['files']},
             'weight_files': asset['weights']['files'], 'config': 'config.json'}
    value = {'schema_version': '1.0', 'model': model, 'runtime': runtime,
            'service': {'ownership': 'owned', 'endpoint': base_url(HOST, port),
                        'retention': 'keep', 'timeout_seconds': 3600, 'weight_markers': recipe['weight_markers'],
                        'command': command(task, asset, recipe, allocation, port, context),
                        'docker': {'image_id': recipe.get('derived_image_id', image['image_id']), 'name': task['environment']['container_name'],
                                   'devices': devices, 'mounts': mounted, **recipe['container_resources']}},
            'registration': recipe['registration_check'], 'npu': {'devices': allocation['devices']},
            'required_features': ['text', 'chat', 'stream'], 'cases': cases(task['model']['model_name'], functional)}
    if 'build' in recipe:
        value['build'] = copy.deepcopy(recipe['build'])
    return value


def capacity_spec(recipe, asset, context):
    for key in ('max_num_seqs', 'model_max_context'):
        if type(recipe[key]) is not int or recipe[key] < 1:
            raise ValueError('部署配方缺少明确正整数 ' + key)
    sizes = {row['path']: row['size'] for row in asset['files']}
    weight = sum(sizes[name] for name in asset['weights']['files'])
    spec = {**recipe['capacity_spec'], 'context_tokens': context}
    if type(spec['weight_bytes']) is not int or spec['weight_bytes'] < weight:
        raise ValueError('显存配方的权重预算小于本次实际权重文件大小')
    return spec
