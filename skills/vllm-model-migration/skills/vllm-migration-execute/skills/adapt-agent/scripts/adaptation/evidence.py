"""稳定文件身份及原子证据；模型 tensor 不进入内存。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .redaction import redact


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:raise ValueError('JSON 重复字段：' + key)
        result[key] = value
    return result


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def stable(path, *, content=False, limit=16 * 1024 * 1024):
    path = Path(path)
    if path.resolve() != path:raise ValueError('文件不得经符号链接重定向：' + str(path))
    digest = hashlib.sha256()
    parts = []
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if content and before.st_size > limit:raise ValueError('资料超过大小限制')
        while True:
            data = stream.read(1024 * 1024)
            if not data:break
            digest.update(data)
            if content:parts.append(data)
        after = os.fstat(stream.fileno())
    stamps = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in (before, after, path.stat())]
    if stamps[0] != stamps[1] or stamps[1] != stamps[2]:raise ValueError('文件读取期间变化：' + str(path))
    return (b''.join(parts) if content else None), digest.hexdigest()


def bounded_json(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:escaped = False
            elif char == '\\':escaped = True
            elif char == '"':quoted = False
        elif char == '"':quoted = True
        elif char in '[{':
            depth += 1
            if depth > 128:raise ValueError('JSON 嵌套超过128层限制')
        elif char in ']}':depth -= 1
    try:return json.loads(text, object_pairs_hook=pairs)
    except RecursionError as error:raise ValueError('JSON 解析递归超限') from error


def read_json(path):
    raw, digest = stable(Path(path), content=True)
    value = bounded_json(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):raise ValueError('JSON 根须为对象')
    return value, digest


def bound(item):
    value, digest = read_json(item['path'])
    if digest != item['sha256']:raise ValueError('资料 SHA256 不一致：' + item['path'])
    return value


def write_json(path, value):
    value = redact(value)
    descriptor, temporary = tempfile.mkstemp(prefix='.adapt-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
