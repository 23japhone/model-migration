"""从逻辑前向打开图重放及原件，局部错误不会丢弃其他层证据。"""
from pathlib import Path

from ....evidence import read_bound
from ..index import decoded
from ..tensors import file_ref
from ..timeline import select_entry
from .native import call_info


def verify(ref, errors, label):
    try:
        path = Path(ref['path'])
        if file_ref(path, path.parent)['sha256'] != ref['sha256']:
            raise ValueError('文件摘要变化')
        return True
    except (OSError, ValueError) as error:
        errors.append(f'{label} {ref["path"]} {error}')
        return False


def frame_entry(entries, request):
    if 'prompt_position' in request or 'forward_id' in request:
        return select_entry(entries, request)
    index = request.get('output_index', 1)
    matches = [item for item in entries if item['output_index'] == index]
    if len(matches) != 1:
        raise ValueError('图时间线缺少所选输出')
    return matches[0]


def lookup(receipt, request):
    if receipt['request_id'] != request['request_id'] or request.get('rank', 0) != 0:
        raise ValueError('图回执与请求或 rank 不符')
    if set(request) & {'stack','event_index','image_index','image_snapshot'}:
        raise ValueError('图回执只支持单卡文本')
    if receipt.get('inference_status') == 'failed':
        errors = []
        for ref in [*receipt['http'].values(), *receipt['forwards']]:
            verify(ref, errors, '失败请求原件')
        return {**receipt, 'cache_errors': errors, 'cache_status': 'incomplete'}
    ready = read_bound(receipt['worker'])
    errors = list(receipt['cache_errors'])
    for label, ref in receipt['http'].items():
        verify(ref, errors, label)
    if request.get('view') == 'timeline':
        for item in receipt['timeline']:
            verify(item['row'], errors, item['forward_id'])
        return {**receipt, 'cache_status': 'incomplete' if errors else 'complete', 'cache_errors': errors}
    return read_frame(receipt, request, ready, errors)


def selected_layer(ready, request):
    names = ready['layers']
    layer = request.get('layer')
    if 'layer_index' in request:
        if request['layer_index'] >= len(names):
            raise ValueError('图回执层号越界')
        layer = names[request['layer_index']]
    if layer is not None and layer not in names:
        raise ValueError('未知文本层 '+layer)
    return layer


def verify_snapshots(result, request, errors, layer):
    row = result
    layers = result['layers']
    for data in layers:
        for name, ref in data['files'].items():
            ok = verify(ref, errors, f"{row['forward_id']}/{data['index']}/{name}")
            if ok and name == request.get('cache_snapshot') and layer is not None:
                result['cache_snapshot'] = decoded(ref, allow_nan=True)
    if 'cache_snapshot' in request and 'cache_snapshot' not in result:
        errors.append(f"{row['forward_id']}/{layer}/{request['cache_snapshot']} 缺少可读原值")


def native_files(native, layer, request, row, errors):
    refs = native.get('files', []) if layer is None else native.get('layers', {}).get(layer, [])
    if 'api' in request:
        refs = [f for f in refs if matches_call(f, request)]
        if not refs:
            raise ValueError('本图没有所选 API 的原生文件')
    return [f for f in refs if verify(f, errors, row['forward_id']+' msProbe')]


def matches_call(ref, request):
    info = ref if 'call' in ref else call_info(Path(ref['path']).name)
    call = info['call']
    return bool(call and request['api'] in (call, call.rsplit('.', 1)[-1])
                and info['occurrence'] == request.get('occurrence', 0))


def read_frame(receipt, request, ready, errors):
    entry = frame_entry(receipt['timeline'], request)
    row = read_bound(entry['row'])
    if any(entry[k] != row[k] for k in ('forward_id','schedule_id','output_index','input_ids')):
        raise ValueError('图前向身份与索引不符')
    for replay in row['replays']:
        if 'capture' in replay:
            verify(replay['capture'], errors, replay['graph_id'])
        else:
            errors.append(row['forward_id']+' 缺少已验证的捕图记录')
    native = row.get('native', {})
    layer = selected_layer(ready, request)
    valid = native_files(native, layer, request, row, errors)
    layers = [data for data in row['layers'] if layer is None or data['path'] == layer]
    result = {**row, 'native': {k:v for k,v in native.items() if k not in ('files','layers')},
              'native_files': valid, 'layers': layers}
    verify_snapshots(result, request, errors, layer)
    return {**result, 'cache_status': 'incomplete' if errors else 'complete', 'cache_errors': errors}
