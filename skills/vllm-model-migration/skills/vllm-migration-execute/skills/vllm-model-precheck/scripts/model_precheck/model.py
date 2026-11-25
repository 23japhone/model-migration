"""读取模型原始配置，并核对可选准备交接的当前 config 身份。"""
from pathlib import Path

from .evidence import read_bytes, read_json


def nested_configs(config, prefix=''):
    values = []
    for name, value in config.items():
        if isinstance(value, dict):
            path = prefix + name
            if name.endswith('_config'):
                values.append({'path': path, 'model_type': value.get('model_type'),
                               'architectures': value.get('architectures'),
                               'dtype': value.get('torch_dtype', value.get('dtype'))})
            values.extend(nested_configs(value, path + '.'))
    return values


def declared_block_stack(config, name, count):
    types = config.get('layer_types')
    if types is None:
        status = 'runtime_resolution_required'
    elif not isinstance(types, list) or not all(isinstance(t, str) and t for t in types):
        status = 'invalid_layer_types'
    else:
        status = 'declared' if len(types) == count else 'count_mismatch'
    labels = types if status == 'declared' else []
    diagram = '\n'.join([f'{name}  ({count})'] + [f'  {i}  {kind}' for i, kind in enumerate(labels)])
    return {'config_path': name, 'count': count, 'layer_types': types, 'status': status, 'diagram': diagram}


def declared_stacks(config, path=''):
    """仅展开配置明确声明的层数和类型，运行实例由 worker 独立核对。"""
    stacks = []
    count = next((config[key] for key in ('num_hidden_layers', 'n_layer', 'num_layers', 'depth')
                  if type(config.get(key)) is int and config[key] > 0), None)
    if count is not None:
        stacks.append(declared_block_stack(config, path or 'root', count))
    for name, value in config.items():
        if name.endswith('_config') and isinstance(value, dict):
            stacks.extend(declared_stacks(value, f'{path}.{name}'.strip('.')))
    return stacks


def inspect_model(request):
    model = request['model']
    root = Path(model['path'])
    config, digest = read_json(root / 'config.json')
    architectures = config.get('architectures', [])
    if not isinstance(architectures, list) or any(not isinstance(a, str) or not a for a in architectures):
        raise ValueError('原始 architectures 必须为非空字符串构成的列表')
    if len(architectures) != len(set(architectures)):
        raise ValueError('architectures 含重复项')
    result = {'path': str(root), 'config': config, 'config_sha256': digest,
              'architectures': architectures, 'nested_configs': nested_configs(config),
              'declared_stacks': declared_stacks(config),
              'task': model['task'], 'modalities': model['modalities'],
              'requested_dtype': model['dtype'], 'requested_quantization': model['quantization'],
              'configured_quantization': config.get('quantization_config'), 'errors': []}
    if not architectures:
        result['errors'].append('缺少根 architectures，不能把嵌套骨干架构提升为整个模型')
    if model['task'] == 'chat':
        check_chat_template(root, result)
    check_quantization(model, config, result)
    check_processors(root, model['modalities'], result)
    if 'prepare_handoff' in request:
        result['prepare_handoff'] = inspect_handoff(request, digest)
    return result


def check_chat_template(root, result):
    problems = []
    for name in ('tokenizer_config.json', 'processor_config.json', 'chat_template.json'):
        path = root / name
        if not path.exists():
            continue
        try:
            config, digest = read_json(path)
            if valid_template(config.get('chat_template')):
                result['chat_template'] = {'path': str(path), 'sha256': digest}
                return
        except (OSError, ValueError) as error:
            problems.append(str(error))
    for path in [root / 'chat_template.jinja', *sorted((root / 'additional_chat_templates').glob('*.jinja'))]:
        if path.is_file() and read_bytes(path).decode('utf-8-sig').strip():
            result['chat_template'] = {'path': str(path)}
            return
    result['errors'].append('chat 缺少可用的 chat_template；' + '；'.join(problems))


def valid_template(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value.values())
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, dict) and isinstance(item.get('template'), str)
        and item['template'].strip() for item in value)


def check_quantization(model, config, result):
    value = config.get('quantization_config')
    if value is not None:
        if not isinstance(value, dict) or not isinstance(value.get('quant_method'), str):
            result['errors'].append('量化 quantization_config 无法识别，不能假定无量化')
        elif model['quantization'] != value['quant_method']:
            result['errors'].append('请求量化方式与原始 config 不一致')
    for item in config.values():
        if isinstance(item, dict):
            check_quantization(model, item, result)


def check_processors(root, modalities, result):
    if not set(modalities) - {'text'}:
        return
    configs = []
    for name in ('processor_config.json', 'preprocessor_config.json', 'video_preprocessor_config.json'):
        if (root / name).is_file():
            config, digest = read_json(root / name)
            if config:
                configs.append({'path': str(root / name), 'sha256': digest})
    result['processor_configs'] = configs
    if not configs:
        result['errors'].append('所需模态缺少 processor/preprocessor 配置资料')


def inspect_handoff(request, config_digest):
    handoff, digest = read_json(request['prepare_handoff'])
    if handoff.get('schema_version') != '1.0' or handoff.get('target') != request['target']:
        raise ValueError('准备交接版本或目标与本次预检不一致')
    phase_path = receipt_path(handoff['phase_manifest'], request['prepare_handoff'])
    phase, phase_digest = read_json(phase_path)
    check_receipt(phase, 'vllm-migration-prepare', handoff['run_id'], 'preparation')
    if object_field(handoff, 'conclusions').get('preparation') != 'pass':
        raise ValueError('准备交接未完成本次准备范围')
    validate_phase_model(phase, handoff, request['target'])
    manifest_path, manifest, actual = model_receipt(handoff, request['prepare_handoff'])
    asset = manifest.get('asset', {})
    validate_handoff_asset(asset, request['model'], config_digest)
    return {'path': request['prepare_handoff'], 'sha256': digest,
            'model_manifest': str(manifest_path), 'model_manifest_sha256': actual,
            'phase_manifest': str(phase_path), 'phase_manifest_sha256': phase_digest,
            'scope': asset.get('scope'), 'note': '仅重新核对 config，未重新核验完整权重'}


def receipt_path(value, handoff_path):
    path = Path(value)
    return path if path.is_absolute() else Path(handoff_path).parent / path


def validate_phase_model(phase, handoff, target):
    if object_field(phase, 'inputs').get('target') != target:
        raise ValueError('准备阶段实际 target 与本次用途不一致')
    task = object_field(object_field(phase, 'tasks'), 'model')
    asset = object_field(object_field(handoff, 'assets'), 'model')
    for key in ('status', 'manifest', 'manifest_sha256', 'conclusions'):
        if task.get(key) != asset.get(key):
            raise ValueError('准备交接模型与阶段实际回执不一致：' + key)


def model_receipt(handoff, handoff_path):
    model = object_field(object_field(handoff, 'assets'), 'model')
    if model.get('status') != 'completed' or object_field(model, 'conclusions').get('assets') != 'pass':
        raise ValueError('准备交接未提供通过的模型资料')
    manifest_path = receipt_path(model['manifest'], handoff_path)
    manifest, actual = read_json(manifest_path)
    if actual != model.get('manifest_sha256'):
        raise ValueError('准备模型回执摘要不一致')
    check_receipt(manifest, 'vllm-model-prepare', handoff['run_id'], 'assets')
    return manifest_path, manifest, actual


def validate_handoff_asset(asset, model, config_digest):
    if not isinstance(asset, dict):
        raise ValueError('准备 asset 必须为对象')
    if Path(asset.get('path', '')).resolve() != Path(model['path']):
        raise ValueError('准备模型路径与本次请求不一致')
    records = asset.get('files', [])
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError('准备 files 必须为文件记录列表')
    config = next((item for item in records if item.get('path') == 'config.json'), None)
    if not config or config.get('sha256') != config_digest:
        raise ValueError('准备回执中的 config 身份与当前内容不一致')
    if asset.get('task') is not None and asset['task'] != model['task']:
        raise ValueError('准备模型 task 与预检用途不一致')
    if not set(model['modalities']).issubset(set(asset.get('modalities', []))):
        raise ValueError('准备模型模态声明不覆盖本次用途')


def check_receipt(value, task_id, run_id, conclusion):
    valid = value.get('schema_version') == '1.0' and value.get('task_id') == task_id
    valid &= value.get('run_id') == run_id and value.get('status') == 'completed'
    valid &= object_field(value, 'conclusions').get(conclusion) == 'pass'
    if not valid:
        raise ValueError('准备回执身份或状态不满足交接条件：' + task_id)


def object_field(value, key):
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError('准备回执字段必须为对象：' + key)
    return item
