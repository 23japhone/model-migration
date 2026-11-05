"""稳定资料、输出隐私与原子阶段证据。"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class EvidenceError(OSError):
    """证据基础失败，停止后续效果。"""


def safe(value):
    text = str(value)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)[^\r\n]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)((?:password|token|secret|api[_-]?key)\s*[:=]\s*)[^\s,;]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@', r'\1', text)
    return re.sub(r'(?i)([a-z][a-z0-9+.-]*://[^\s?#]+)[?#][^\s]*', r'\1', text)


def sanitized(value):
    if isinstance(value, dict):
        secrets = {'password','token','access_token','secret','api_key','authorization','credentials'}
        return {key:'[已隐藏]' if key.lower() in secrets else sanitized(item) for key,item in value.items()}
    if isinstance(value, list):return [sanitized(item) for item in value]
    return safe(value) if isinstance(value, str) else value


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def digest(path):
    path = Path(path)
    if path.resolve() != path:raise ValueError('资料路径不能经符号链接重定向')
    result = hashlib.sha256()
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        for data in iter(lambda:stream.read(1024*1024),b''):result.update(data)
        after = os.fstat(stream.fileno())
    stamps = [(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns) for s in [before,after,path.stat()]]
    if stamps[0] != stamps[1] or stamps[1] != stamps[2]:raise ValueError('资料读取期间变化')
    return result.hexdigest()


def pairs(items):
    result = {}
    for key,value in items:
        if key in result:raise ValueError('JSON 重复字段')
        result[key] = value
    return result


def read(path):
    path = Path(path)
    if path.stat().st_size > 64*1024*1024:raise ValueError('资料超过64 MiB限制')
    before = digest(path)
    try:value = json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=pairs)
    except RecursionError as error:raise ValueError('JSON 嵌套超限') from error
    if before != digest(path):raise ValueError('JSON 读取期间变化')
    if not isinstance(value,dict):raise ValueError('JSON 根必须为对象')
    return value


def bound(reference):
    value = read(reference['path'])
    if digest(reference['path']) != reference['sha256']:raise ValueError('引用资料 SHA256 不一致')
    return value


def ref(path):return {'path':str(path),'sha256':digest(path)}


def write(path,value,*,redact=True):
    path = Path(path);temporary = None
    try:
        fd,temporary = tempfile.mkstemp(prefix='.stage-',dir=path.parent)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(sanitized(value) if redact else value,stream,ensure_ascii=False,indent=2);stream.write('\n')
        os.replace(temporary,path)
    except OSError as error:raise EvidenceError('阶段证据保存失败：'+safe(error)) from error
    finally:
        if temporary and os.path.exists(temporary):os.unlink(temporary)
