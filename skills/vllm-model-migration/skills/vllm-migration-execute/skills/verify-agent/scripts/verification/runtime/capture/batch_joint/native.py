"""只按有来源证明的张量轴从原生物理批次导出成员数值。"""

from pathlib import Path
import tempfile

from ..tensors import file_ref


def parameter(argument, parameters):
    if len(argument) != 2:
        return None
    side, key = argument
    if side == 'input_kwargs':
        return key
    if side == 'input_args' and type(key) is int and 0 <= key < len(parameters):
        return parameters[key]
    return None


def projection_view(call, view, row, total):
    """The audited dense QKV projection applies linear to the packed hidden rows without reordering."""
    import re

    parent = re.escape(call['layer'] + '.self_attn.qkv_proj')
    if not re.fullmatch(r'Module\.' + parent + r'\.(?:Ascend)?QKVParallelLinear\.forward\.\d+', call.get('parent') or ''):
        return view
    shapes = {tuple(t['argument']): t['shape'] for side in call['tensors'].values() for t in side}
    data, weight, output = (shapes.get(key, []) for key in [('input_args', 0), ('input_args', 1), ('output', 0)])
    if len(data) != 2 or len(weight) != 2 or data != [total, weight[1]] or output != [total, weight[0]]:
        return view
    arg = view['argument']
    if arg in (['input_args', 0], ['output', 0]):
        view.update(scope='member_tokens', axis=0, range=row['token_range'])
    if arg == ['input_args', 1] or (arg == ['input_args', 2] and view['shape'] == [weight[0]]):
        view.update(scope='shared_parameter')
    return view


def decoder_axis(arg, shape, parameters):
    name = parameter(arg, parameters)
    if name == 'positions' and len(shape) in (1, 2):
        return len(shape) - 1
    outputs = (['output', 0], ['output', 0, 0], ['output', 0, 1], ['output', 1])
    if len(shape) == 2 and (name in ('hidden_states', 'residual') or arg in outputs):
        return 0
    return None


def member_view(call, tensor, row, signatures, total):
    arg, shape = tensor['argument'], tensor['shape']
    view = {'scope': 'batch_only', 'argument': arg, 'dtype': tensor['dtype'], 'shape': shape}
    if tensor.get('unavailable'):
        return {**view, 'scope': 'unavailable'}
    if call.get('module') and arg[0] == 'parameters':
        return {**view, 'scope': 'shared_parameter'}
    if call.get('api') == 'Functional.linear' and call.get('layer') in signatures:
        return projection_view(call, view, row, total)
    module = call.get('module')
    if not module or module != call.get('layer') or module not in signatures:
        return view
    axis = decoder_axis(arg, shape, signatures[module])
    if axis is not None and shape[axis] == total:
        view.update(scope='member_tokens', axis=axis, range=row['token_range'])
    return view


def describe(calls, row, signatures, total):
    for call in calls:
        views = [
            member_view(call, item, row, signatures, total) for side in call['tensors'].values() for item in side
        ]
        yield {k: call[k] for k in ('call', 'module', 'api', 'occurrence')} | {'tensors': views}


def export(item, view, target):
    import torch

    if view['scope'] not in ('member_tokens', 'shared_parameter'):
        raise ValueError('该张量缺少可证明的成员轴')
    path = Path(item['path'])
    if file_ref(path, path.parent)['sha256'] != item['sha256']:
        raise ValueError('msProbe 原件摘要变化')
    tensor = torch.load(path, weights_only=True, map_location='cpu')
    if not isinstance(tensor, torch.Tensor):
        raise ValueError('原件不是可读取的 PyTorch 张量')
    if list(tensor.shape) != item['shape'] or str(tensor.dtype).split('.')[-1] != item['dtype'].split('.')[-1]:
        raise ValueError('msProbe 张量形状或 dtype 与清单不同')
    if view['scope'] == 'member_tokens':
        start, stop = view['range']
        tensor = tensor.narrow(view['axis'], start, stop - start)
    local = tensor.contiguous()
    target = Path(target)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='member-', suffix='.bin', dir=target, delete=False) as stream:
        result = Path(stream.name).absolute()
        try:
            stream.write(memoryview(local.view(torch.uint8).numpy()).cast('B'))
            stream.flush()
        except BaseException:
            result.unlink(missing_ok=True)
            raise
    return {
        **file_ref(result, target.absolute()),
        'shape': list(local.shape),
        'dtype': str(local.dtype),
        'source_sha256': item['sha256'],
        'bytes': result.stat().st_size,
        'scope': view['scope'],
        **{key: view[key] for key in ('axis', 'range') if key in view},
    }
