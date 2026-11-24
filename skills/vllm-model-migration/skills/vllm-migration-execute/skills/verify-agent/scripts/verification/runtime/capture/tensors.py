"""解析 msProbe 原生 construct.json 的父调用与 dump.json 参数树。"""
from pathlib import Path
import re

from ...evidence import sha256
from .adapters.state import read


def file_ref(path, root):
    if path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'张量文件缺失或超出采集目录 {path}')
    return {'path': str(path), 'sha256': sha256(path)}


def arguments(value, location, folder, token_row, module_layer):
    if value is None or isinstance(value, (str, int, float, bool)) or value in ([], {}):
        yield {'argument': location, 'type': type(value).__name__, 'value': value}
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from arguments(item, [*location, i], folder, token_row, module_layer)
    elif isinstance(value, dict) and 'data_name' in value:
        yield tensor_ref(value, location, folder, token_row, module_layer)
    elif isinstance(value, dict):
        if 'shape' in value and 'dtype' in value:
            if all(value['shape']):
                raise ValueError(f'张量缺少原始数据文件 {location}')
            yield {'argument': location, 'shape': value['shape'], 'dtype': value['dtype'],
                   'elements': 0, 'slice': 'whole_tensor'}
            return
        if 'type' in value and 'value' in value:
            yield {'argument': location, 'type': value['type'], 'value': value['value']}
            return
        for k, item in value.items():
            yield from arguments(item, [*location, k], folder, token_row, module_layer)


def tensor_ref(value, location, folder, token_row, module_layer):
    name = value['data_name']
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError(f'非法 msProbe 张量文件名 {name}')
    shape = value['shape']
    # 该 runner 的 decoder block forward(hidden_states, residual, positions) 使用扁平 token 行。
    axis = None
    if module_layer and len(shape) == 2 and location[0] == 'output':
        axis = 0
    if module_layer and len(shape) in (1, 2) and location == ['input_kwargs', 'positions']:
        axis = len(shape) - 1
    if module_layer and len(shape) == 2 and location[:2] in (
            ['input_kwargs', 'hidden_states'], ['input_kwargs', 'residual']):
        axis = 0
    selected = {'axis': axis, 'index': token_row} if axis is not None else 'whole_tensor'
    return {'argument': location, 'shape': shape, 'dtype': value['dtype'], 'slice': selected,
           **file_ref(folder / 'dump_tensor_data' / name, folder)}


def module_path(call):
    match = re.fullmatch(r'Module\.(.+)\.[^.]+\.forward\.\d+', call)
    return match.group(1) if match else None


def owner(call, parents, layers):
    seen = set()
    while call:
        if call in seen:
            raise ValueError('msProbe construct.json 存在循环')
        seen.add(call)
        name = module_path(call)
        if name in layers:
            return name
        call = parents.get(call)
    return None


def literals(arguments):
    return [v for side in arguments.values() for v in side if 'value' in v]


def tensor_only(arguments):
    return {k: [v for v in side if 'shape' in v] for k, side in arguments.items()}


def has_io(tensors):
    raw = tensor_only(tensors)
    return bool(raw['output'] and (raw['input_args'] or raw['input_kwargs']))


def linear_slices(tensors, row, positions):
    # Linear 对最后一轴投影；只有真实输入、输出的首维与本次 token 行均吻合才标注。
    data = [v for v in tensors['input_args'] if v['argument'] == ['input_args', 0]]
    output = [v for v in tensors['output'] if v['argument'] == ['output', 0]]
    if len(data) == 1 and len(output) == 1 and all(
            len(v.get('shape', ())) == 2 and v['shape'][0] == len(positions) for v in [*data, *output]):
        for v in [*data, *output]:
            v['slice'] = {'axis': 0, 'index': row}


def call_arguments(call, data, folder, row, module_layer):
    if 'output' not in data or not ({'input_args', 'input_kwargs'} & data.keys()):
        raise ValueError('调用输入输出字段缺失：' + call)
    result = {side: list(arguments(data[side], [side], folder, row, module_layer)) if side in data else []
              for side in ('input_args', 'input_kwargs', 'output')}
    if not (result['input_args'] or result['input_kwargs']) or not result['output']:
        raise ValueError('调用输入输出无法解析：' + call)
    return result


def inventory(folder, layers, row, positions=(), *, require_layer_api=True):
    dump = read(folder / 'dump.json')['data']
    parents = read(folder / 'construct.json')
    result = []
    covered, apis, occurrences = set(), set(), {}
    for call, data in dump.items():
        layer = owner(call, parents, layers)
        module = module_path(call)
        tensors = call_arguments(call, data, folder, row, layer is not None and module == layer)
        if layer and module == layer:
            if not has_io(tensors):
                raise ValueError(f'层输入或输出未落盘 {call}')
            covered.add(layer)
        if layer and module is None and has_io(tensors):
            apis.add(layer)
        api = None if module else call.rsplit('.', 2)[0]
        if api == 'Functional.linear':
            linear_slices(tensors, row, positions)
        key = (layer, module or api)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        result.append({'call': call, 'module': module, 'layer': layer, 'parent': parents.get(call),
                       'api': api, 'occurrence': occurrence, 'tensors': tensor_only(tensors),
                       'values': literals(tensors)})
    if set(layers) != covered or (require_layer_api and set(layers) != apis):
        raise ValueError(f'缺少层输入输出 {sorted(set(layers)-covered)} 或层内 API {sorted(set(layers)-apis)}')
    return result
