"""成员读接口只导出该成员的切片或该成员保存的物理缓存槽。"""

from pathlib import Path

from .... import outputs
from ....evidence import read_bound
from ..batch_identity.receipt import lookup as identity_lookup
from ..cache_evidence import verify_files
from ..tensors import file_ref
from . import catalog, native
from .receipt import FAILURES


def validate(request):
    if 'native_tensor' not in request:
        return
    value = request['native_tensor']
    if (
        not isinstance(value, dict)
        or set(value) != {'call', 'argument'}
        or not isinstance(value['call'], str)
        or not value['call']
        or 'layer_index' not in request
        or 'cache_snapshot' in request
    ):
        raise ValueError('native_tensor 需要 layer_index、确切调用和参数路径')
    arg = value['argument']
    if (
        not isinstance(arg, list)
        or not arg
        or arg[0] not in ('input_args', 'input_kwargs', 'output', 'parameters')
        or any(type(k) not in (int, str) or (type(k) is int and k < 0) for k in arg[1:])
    ):
        raise ValueError('native_tensor.argument 无效')


def snapshot(layer, directory, request):
    occurrence = request.get('occurrence', 0)
    (call,) = [c for c in layer['invocations'] if c['occurrence'] == occurrence]
    name = request['cache_snapshot']
    root = directory / ('call-' + str(occurrence))
    files = [(root, call.get('boundary', {}), None)]
    files.extend((root / f'state-{i}', s['snapshots'], s['event']) for i, s in enumerate(call['states']))
    matches = [(p, items[name], event) for p, items, event in files if name in items]
    if len(matches) != 1:
        raise ValueError(f'缺少唯一快照 {name}，调用序号 {occurrence}')
    ((base, item, plan),) = matches
    verify_files(base, {name: item}, {name})
    return {'cache_snapshot': {**item, 'name': name}, 'cache_plan': plan}


def selected_native(request, calls, views, target):
    sel = request['native_tensor']
    (call,) = [c for c in calls if c['call'] == sel['call']]
    (view,) = [
        v for c in views if c['call'] == sel['call'] for v in c['tensors'] if v['argument'] == sel['argument']
    ]
    (item,) = [t for t in call['tensors'][sel['argument'][0]] if t['argument'] == sel['argument']]
    return native.export(item, view, target)


def inspect_layer(row, num, request, folder, target):
    index = request['layer_index']
    layer = read_bound(num['layers'][str(index)])
    if index != layer['index'] or any(layer[k] != row[k] for k in ('request_id', 'group_id', 'forward_id')):
        raise ValueError('封存的层号不同')
    result = {'layer': layer['layer'], 'layer_index': index, 'layer_errors': layer['errors']}
    if 'cache_snapshot' in request:
        directory = folder / ('cache-' + row['forward_id']) / ('layer-' + str(index))
        result.update(snapshot(layer['cache'], directory, request))
    else:
        if 'cache' in layer:
            result['cache'] = layer['cache']
        calls = catalog.calls_for(layer['native'], row, layer['layer'])
        views = list(native.describe(calls, row, num['signatures'], num['total_tokens']))
        result['calls'] = views
        if 'native_tensor' in request:
            for ref in num['native_metadata']:
                read_bound(ref)
            result['native_tensor'] = selected_native(request, calls, views, target)
    return result


def read_member(result, request, ref, target):
    num = read_bound(ref)
    if any(result[k] != num[k] for k in ('request_id', 'group_id', 'forward_id')):
        raise ValueError('成员数值清单身份不符')
    result.update(
        layer_indices=[int(i) for i in num['layers']],
        **{k: num[k] for k in ('native_bytes_shared', 'step', 'logits') if k in num},
    )
    if 'worker_numeric' in num:
        try:
            read_bound(num['worker_numeric'])
        except FAILURES as error:
            result.update(status='incomplete', errors=[*result['errors'], f"{result['forward_id']} worker 数值记录 {error}"])
    if 'layer_index' in request:
        result.update(inspect_layer(result, num, request, Path(ref['path']).parent, target))
    if 'logits' in result:
        item = result['logits']
        path = Path(item['path'])
        if file_ref(path, Path(ref['path']).parent)['sha256'] != item['sha256']:
            del result['logits']
            raise ValueError('成员 logits 摘要变化')


def identity_query(request):
    numeric = {'layer_index', 'native_tensor', 'cache_snapshot', 'occurrence'}
    query = {k: v for k, v in request.items() if k not in numeric}
    if 'occurrence' in request and 'cache_snapshot' not in request:
        raise ValueError('成员 occurrence 仅用于选择同一层的缓存调用')
    if set(request) & {'cache_snapshot', 'occurrence'} and 'layer_index' not in request:
        raise ValueError('成员缓存查询需要明确 layer_index')
    return query


def lookup(receipt, request, target):
    result = identity_lookup(receipt, identity_query(request))
    result.update({k: receipt[k] for k in ('cache_capture', 'request_wall_seconds') if k in receipt})
    if 'timeline' in result:
        result['timeline'] = [{k: v for k, v in r.items() if k != 'numeric'} for r in result['timeline']]
        return result
    result['identity_status'] = result['status']
    result['status'] = receipt['status']
    ref = result.pop('numeric', None)
    try:
        if ref is None:
            raise ValueError('本成员前向缺少数值文件')
        if 'native_tensor' in request:
            declared = read_bound(receipt['source'])['inputs']
            runtime = Path(ref['path']).parents[2]
            outputs.check(target, [runtime, *outputs.protected_roots(declared)])
        read_member(result, request, ref, target)
    except FAILURES as error:
        result.update(
            status='incomplete',
            errors=[
                *result['errors'],
                f'{result["forward_id"]} {result["request_id"]} layer {request.get("layer_index")} {error}',
            ],
        )
    return result
