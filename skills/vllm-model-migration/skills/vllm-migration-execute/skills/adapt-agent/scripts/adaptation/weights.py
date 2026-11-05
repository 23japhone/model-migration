"""完整 safetensors key/shape/dtype 及显式融合区间；不执行模型。"""
from collections import defaultdict
import math
import os
from pathlib import Path
import struct

from .evidence import bound, bounded_json, read_json

WIDTHS = {'BF16': 2, 'F16': 2, 'F32': 4, 'F64': 8, 'I64': 8, 'I32': 4, 'I16': 2,
          'I8': 1, 'U8': 1, 'BOOL': 1}


def shape(value):
    if not isinstance(value, list) or any(type(v) is not int or v < 0 for v in value):
        raise ValueError('shape 须为非负整数列表')
    return value


def header(path):
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        size_raw = stream.read(8)
        if len(size_raw) != 8:raise ValueError('safetensors 文件头截断')
        size = struct.unpack('<Q', size_raw)[0]
        if size > 16 * 1024 * 1024 or size > before.st_size - 8:raise ValueError('safetensors header 过大或截断')
        value = bounded_json(stream.read(size).decode())
        after = os.fstat(stream.fileno())
    stamps = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in (before, after)]
    if stamps[0] != stamps[1]:raise ValueError('权重头读取期间发生变化')
    return tensor_records(value, before.st_size - size - 8)


def tensor_records(value, data_size):
    if not isinstance(value, dict):raise ValueError('safetensors header 须为对象')
    tensors, ranges = {}, []
    for name, item in value.items():
        if name == '__metadata__':continue
        if not isinstance(item, dict):raise ValueError('tensor header 须为对象')
        dimensions = shape(item['shape'])
        dtype = item['dtype']
        start, end = item['data_offsets']
        if dtype not in WIDTHS:raise ValueError('未支持的权重 dtype：' + str(dtype))
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise ValueError('权重 data_offsets 无效')
        if end - start != math.prod(dimensions) * WIDTHS[dtype]:raise ValueError('tensor shape 与字节长度不匹配')
        ranges.append((start, end))
        tensors[name] = {'shape': dimensions, 'dtype': dtype}
    previous = 0
    for start, end in sorted(ranges):
        if start != previous:raise ValueError('tensor 区间重叠或有空洞')
        previous = end
    if previous != data_size:raise ValueError('权重数据区截断或存在尾部数据')
    return tensors


def checkpoint(model):
    root = Path(model['root'])
    result, owners = {}, {}
    for name in model['weight_files']:
        observed = header(root / name)
        if result.keys() & observed.keys():raise ValueError('多个 shard 包含重复 tensor')
        result.update(observed)
        owners.update({key: name for key in observed})
    for index in root.glob('*.index.json'):
        relative = str(index.relative_to(root))
        if relative not in model['files']:raise ValueError('权重 index 未固定身份')
        value, _ = read_json(index)
        if value.get('weight_map') != owners:raise ValueError('index 与实际 shard/key 集合不一致')
    if not result:raise ValueError('权重 tensor 不能为空')
    return result


def mapped_piece(item, tensors, destinations):
    if not isinstance(item, dict) or set(item) != {'source', 'destination', 'shape', 'axis', 'offset'}:
        raise ValueError('权重映射字段无效')
    name, target = item['source'], item['destination']
    if name not in tensors or target not in destinations:raise ValueError('源或目的 tensor 未声明')
    source_shape = shape(item['shape']);destination = destinations[target]
    if source_shape != tensors[name]['shape']:raise ValueError('源 tensor shape 不匹配：' + name)
    if tensors[name]['dtype'] != destination['dtype']:raise ValueError('源与目的 dtype 不匹配')
    target_shape = shape(destination['shape'])
    axis, offset = item['axis'], item['offset']
    if type(offset) is not int or offset < 0:raise ValueError('映射 offset 无效')
    return geometry(source_shape, target_shape, axis, offset)


def geometry(source_shape, target_shape, axis, offset):
    if axis is None:
        if source_shape != target_shape or offset != 0:raise ValueError('直接映射 shape/offset 不匹配')
        return (0, 1, None)
    if type(axis) is not int or not 0 <= axis < len(target_shape):raise ValueError('融合 axis 无效')
    if len(source_shape) != len(target_shape):raise ValueError('融合 rank 不一致')
    if any(a != b for i, (a, b) in enumerate(zip(source_shape, target_shape)) if i != axis):
        raise ValueError('融合非拼接维度不一致')
    return (offset, offset + source_shape[axis], axis)


def destination_coverage(pieces, destinations):
    for target, definition in destinations.items():
        intervals = sorted(pieces[target], key=lambda item: item[0])
        if not intervals:raise ValueError('目的 tensor 没有完整来源：' + target)
        axis = intervals[0][2]
        previous = 0
        for start, end, observed in intervals:
            if observed != axis or start != previous:raise ValueError('目的 tensor 区间重叠或有缺口：' + target)
            previous = end
        expected = 1 if axis is None else definition['shape'][axis]
        if previous != expected:raise ValueError('目的 tensor 未完整覆盖：' + target)


def coverage(mappings, destinations, tensors):
    pieces, seen = defaultdict(list), set()
    for item in mappings:
        piece = mapped_piece(item, tensors, destinations)
        name = item['source']
        if name in seen:raise ValueError('权重 source 重复：' + name)
        seen.add(name)
        pieces[item['destination']].append(piece)
    destination_coverage(pieces, destinations)
    return seen


def inspect(request, mapping_ref, fingerprints, config_sha256):
    mapping = bound(mapping_ref)
    if mapping.get('schema_version') != '1.0':raise ValueError('映射 schema_version 无效')
    if mapping.get('source_fingerprints') != fingerprints or mapping.get('config_sha256') != config_sha256:
        raise ValueError('权重映射未绑定当前源码/config')
    tensors = checkpoint(request['model'])
    destinations, ignored = mapping['destinations'], mapping['ignored']
    if not isinstance(destinations, dict) or not destinations or not isinstance(ignored, dict):
        raise ValueError('目的清单/排除清单无效')
    for item in destinations.values():
        shape(item['shape'])
        if item['dtype'] not in WIDTHS:raise ValueError('目的 dtype 未支持')
    if not isinstance(mapping['mappings'], list):raise ValueError('mappings 须为列表')
    seen = coverage(mapping['mappings'], destinations, tensors)
    if seen & ignored.keys() or seen | ignored.keys() != tensors.keys():raise ValueError('权重 key 存在未知、未覆盖或重复排除')
    if any(not isinstance(reason, str) or not reason.strip() for reason in ignored.values()):
        raise ValueError('每个未启用 tensor 必须说明理由')
    return {'mapped': len(seen), 'destinations': len(destinations), 'ignored': len(ignored),
            'total': len(tensors), 'mapping': mapping_ref,
            'scope': '静态 key/shape/dtype 与区间核验；数学、TP loader 及排除理由由定向测试和运行验收证明'}
