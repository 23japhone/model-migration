"""Read delivery events and follow a selected text step to its image encoder."""
from pathlib import Path
import math
import struct

from ..index import decoded
from ..tensors import file_ref


def validate(request):
    for name in ('image_index', 'event_index'):
        if name in request and (type(request[name]) != int or request[name] < 0):
            raise ValueError(name + ' 须为非负整数')
    if request.get('stack', 'text') not in ('vision', 'text'):
        raise ValueError('stack 须为 text 或 vision')
    if 'event_index' in request and set(request) - {'event_index', 'source', 'request_id'}:
        raise ValueError('按事件查询返回其关联的全部输出与前向')
    validate_snapshot_selector(request)


def validate_snapshot_selector(request):
    if 'image_snapshot' in request:
        name = request['image_snapshot']
        if not isinstance(name, str) or not name or Path(name).name != name or 'rank' not in request:
            raise ValueError('读取视觉原值需要 rank 与有效的快照名')
        if 'cache_snapshot' in request:
            raise ValueError('不能同时选择两种快照')
    if request.get('stack') == 'vision' and 'cache_snapshot' in request:
        raise ValueError('文本缓存不属于视觉栈')


def event_view(result, index):
    events = result.get('delivery', {}).get('events', [])
    if not 0 <= index < len(events):
        raise ValueError('SSE event_index 超出回执范围')
    return {'request_id': result['request_id'], **events[index], 'delivery_complete': result['delivery']['complete']}


def image_for(result, request):
    index = request.get('image_index', 0)
    if not 0 <= index < len(result.get('images', [])):
        raise ValueError('没有所选图像')
    image = result['images'][index]
    directory = Path(result['engine_input']['path']).parent
    if (image['image_index'] != index or Path(image['path']) != directory / f'image-{index}.bin'
            or file_ref(Path(image['path']), directory)['sha256'] != image['sha256']):
        raise ValueError('原始图像文件身份不符')
    return image


def image_detail(row, request, directory):
    from .vision import check_files
    info = row.get('vision')
    if not info:
        raise ValueError('缺少所选前向的视觉原值')
    base = directory / ('vision-' + row['forward_id'])
    if 'image_snapshot' not in request:
        check_files(base, info)
        return {}
    name = request['image_snapshot']
    item = info['snapshots'].get(name)
    if item is None:
        raise ValueError('缺少视觉快照 ' + name)
    check_files(base, {'snapshots': {name: item}})
    item = {**item, 'name': name, 'size': math.prod(item['shape'])}
    if item['dtype'] in ('torch.int64', 'torch.int32'):
        fmt = '<q' if item['dtype'] == 'torch.int64' else '<i'
        item['values'] = [v[0] for v in struct.iter_unpack(fmt, Path(item['path']).read_bytes())]
    else:
        item = decoded(item, allow_nan=True)
    return {'image_snapshot': item}
