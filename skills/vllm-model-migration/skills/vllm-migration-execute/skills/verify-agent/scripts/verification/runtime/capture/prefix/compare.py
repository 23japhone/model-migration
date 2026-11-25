"""按原 dtype 和行边界核对两份已封存的数值文件。"""
import hashlib
from pathlib import Path

from ..tensors import file_ref


def checked(item, verified):
    path = Path(item['path'])
    ref = path, item['sha256'], item['bytes']
    if ref not in verified:
        if path.stat().st_size != item['bytes'] or file_ref(path, path.parent)['sha256'] != item['sha256']:
            raise ValueError(f'文件大小或摘要变化 {path}')
        verified.add(ref)
    return path


def digest(item, start, size, verified):
    path = checked(item, verified)
    if start < 0 or size <= 0 or start + size > item['bytes']:
        raise ValueError('比较范围超出快照有效字节')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        stream.seek(start)
        while size:
            data = stream.read(min(size, 1024 * 1024))
            if not data:
                raise ValueError('数值文件被截断')
            value.update(data)
            size -= len(data)
    return value.hexdigest()


def ranges(writer, reader, row, low, high, route):
    if writer['dtype'] != reader['dtype'] or writer['shape'][1:] != reader['shape'][1:]:
        raise ValueError('两请求的缓存布局或 dtype 不同')
    if route is not None:
        if writer['shape'] != reader['shape'] or not route['copy_ranges']:
            raise ValueError('检查点复制范围或行形状无效')
        cursor = 0
        for src, dst, length in sorted(route['copy_ranges']):
            if src != cursor or dst != cursor or length < 1:
                raise ValueError('GDN 有效状态行复制不连续')
            cursor += length
        if cursor != writer['bytes'] or cursor != reader['bytes']:
            raise ValueError('GDN 有效状态行只复制了部分字节')
        return route['copy_ranges']
    if writer['shape'][0] != len(row['positions']) or reader['shape'][0] < high:
        raise ValueError('快照行数与实际 Token 位置不符')
    stride = writer['bytes'] // writer['shape'][0]
    return [[(low-row['positions'][0])*stride, low*stride, (high-low)*stride]]


def compare(part, verified):
    for a, b, n in part['byte_ranges']:
        if digest(part['source_file'], a, n, verified) != digest(part['consumer_file'], b, n, verified):
            raise ValueError('复用原值与生产请求写后值不同')
