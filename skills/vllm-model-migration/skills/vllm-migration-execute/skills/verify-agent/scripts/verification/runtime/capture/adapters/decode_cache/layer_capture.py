"""在每个逻辑层的实际调用内采边界激活，并安装该实例的缓存观察点。"""
from contextlib import ExitStack
from functools import wraps

from .backends import BY_KIND
from .events import group_for
from .snapshots import observer_only, Snapshots, SnapshotBudget


def floating_tensor(values):
    import torch
    if isinstance(values, torch.Tensor):
        return values if values.is_floating_point() else None
    if isinstance(values, dict):
        values = values.values()
    elif not isinstance(values, (tuple, list)):
        return None
    for value in values:
        tensor = floating_tensor(value)
        if tensor is not None:
            return tensor
    return None


def capture_boundary(store, name, values, invocation):
    try:
        with observer_only():
            tensor = floating_tensor(values)
            if tensor is None:
                raise ValueError('未找到浮点激活张量')
            store.boundary(name, tensor)
    except Exception as error:
        invocation['errors'].append(f'{name}: {error}')


def watch_state(stack, rec, plan, metas, descriptor, store, result):
    name, kind = descriptor['address'], descriptor['kind']
    module = rec.layer_modules[descriptor['module']]
    meta = metas[name]
    group = group_for(plan, rec.runner, name)
    spec = BY_KIND.get(kind)
    if spec is None:
        raise ValueError('未适配的状态后端 ' + descriptor['backend'])
    capture = spec.bind(stack, rec, module, group, plan, meta, store, result)
    result['event'] = capture.event


def install_states(stack, rec, context, row, storage, invocation):
    plan, metas = context
    for index, descriptor in enumerate(row['states']):
        result = {**descriptor, 'ops': [], 'errors': [], 'snapshots': {}}
        invocation['states'].append(result)
        try:
            if descriptor['module'] in rec.backend_errors:
                raise ValueError(rec.backend_errors[descriptor['module']])
            store = storage.scope(f'state-{index}')
            result['snapshots'] = store.files
            watch_state(stack, rec, plan, metas, descriptor, store, result)
        except Exception as error:
            result['errors'].append(str(error))


def bind_block(stack, rec, context, row, storage, record):
    block = rec.layer_modules[row['path']]
    original = block.forward

    @wraps(original)
    def observed(*args, **kwargs):
        ordinal = len(record['invocations'])
        invocation = {'occurrence': ordinal, 'states': [], 'boundary': {}, 'errors': []}
        record['invocations'].append(invocation)
        with ExitStack() as state_stack:
            store = None
            try:
                with observer_only():
                    store = storage.scope(f'call-{ordinal}')
                    invocation['boundary'] = store.files
                    install_states(state_stack, rec, context, row, store, invocation)
            except Exception as error:
                invocation['errors'].append(str(error))
            if store is not None:
                capture_boundary(store, 'input', kwargs.get('hidden_states', (args, kwargs)), invocation)
            result = original(*args, **kwargs)
            if store is not None:
                capture_boundary(store, 'output', result, invocation)
        return result

    if 'forward' in vars(block):
        stack.callback(setattr, block, 'forward', original)
    else:
        stack.callback(delattr, block, 'forward')
    block.forward = observed


def schedule_errors(row, groups):
    attached = {item['address'] for item in row['states']}
    declared = {'full_attention': 'attention', 'linear_attention': 'gdn'}.get(row['declared_type'])
    if declared and row['states'] and row['kind'] != declared:
        return [f"配置声明 {row['declared_type']} 与运行状态后端 {row['kind']} 不符"]
    if missing := groups[row['path']] - attached:
        return ['调度缓存缺少已适配的观察点 ' + ', '.join(sorted(missing))]
    if not attached and row['kind'] == 'unclassified':
        return ['层的状态语义未经识别']
    if not attached and row['kind'] not in ('mlp', 'feed_forward', 'stateless'):
        return ['配置层型缺少状态适配 ' + row['kind']]
    return []


def install_layers(stack, rec, record, plan, metas):
    directory = rec.folder / ('cache-' + rec.frame['forward_id'])
    directory.mkdir(mode=0o700)
    budget = rec.cache_budget
    if 'phase' in plan:
        budget = SnapshotBudget(min(rec.request_remaining, rec.cache_budget - rec.frame.get("vision", {}).get("snapshot_bytes", 0)))
        available = budget.remaining
        def account():
            record['snapshot_bytes'] = available - budget.remaining
            rec.request_remaining -= record['snapshot_bytes']
        stack.callback(account)
    root = Snapshots(directory, budget, rec.cache_watch)
    groups = {row['path']: set() for row in rec.architecture['layers']}
    owners = {s['address']: r['path'] for r in rec.architecture['layers'] for s in r['states']}
    allocated = [name for group in plan['groups'] for name in group['layers']]
    for name in allocated:
        owner = owners.get(name) or next((p for p in groups if name == p or name.startswith(p + '.')), None)
        if owner is None:
            record['errors'].append('调度层缺少结构映射 ' + name)
        else:
            groups[owner].add(name)
    for row in rec.architecture['layers']:
        layer = {'path': row['path'], 'index': row['index'], 'kind': row['kind'],
                 'invocations': [], 'errors': schedule_errors(row, groups)}
        record['layers'].append(layer)
        try:
            store = root.scope(f"layer-{row['index']}")
            bind_block(stack, rec, (plan, metas), row, store, layer)
        except Exception as error:
            layer['errors'].append(str(error))


def layer_errors(layer):
    errors = list(layer['errors'])
    if not layer['invocations']:
        errors.append('目标文本层未执行')
    for call in layer['invocations']:
        errors.extend(call['errors'])
        if set(call['boundary']) != {'input', 'output'}:
            errors.append('层输入输出快照不完整')
        for state in call['states']:
            errors.extend(state['errors'])
            backend = BY_KIND.get(state['kind'])
            if backend is None or state['ops'] != backend.operations(state.get('event', {})):
                errors.append('层状态更新算子缺失或顺序不符 ' + state['module'])
    return errors
