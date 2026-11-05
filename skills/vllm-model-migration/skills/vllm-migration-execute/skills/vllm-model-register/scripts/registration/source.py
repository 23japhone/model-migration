"""只修改显式 registry 的映射，冻结原文和 diff，保留已有权限。"""
import ast
from contextlib import contextmanager
import difflib
import os
from pathlib import Path
import stat
import tempfile

from .evidence import digest, read_bytes, write_json


def proposed_source(request, raw):
    text = raw.decode('utf-8-sig')
    tree = ast.parse(text)
    roots = [node for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'ModelRegistry' for t in node.targets)]
    if len(roots) != 1 or not isinstance(roots[0].value, ast.Call):
        raise ValueError('无法定位唯一 ModelRegistry 构造入口，需目标版本专用注册方式')
    if not isinstance(roots[0].value.func, ast.Name) or roots[0].value.func.id != '_ModelRegistry':
        raise ValueError('ModelRegistry 构造机制与已核对版本不同')
    implementation = request['implementation']
    module = implementation['module'].removeprefix('vllm.model_executor.models.')
    block = ('# vllm-model-register：仅扩展原始 architecture 映射\n'
             '_VLLM_MODELS = {\n    **_VLLM_MODELS,\n'
             f"    {request['model']['architecture']!r}: ({module!r}, {implementation['class_name']!r}),\n}}\n\n")
    lines = text.splitlines(keepends=True)
    lines.insert(roots[0].lineno - 1, block)
    changed = ''.join(lines)
    ast.parse(changed)
    encoded = changed.encode('utf-8')
    if raw.startswith(b'\xef\xbb\xbf'):
        encoded = b'\xef\xbb\xbf' + encoded
    return encoded


@contextmanager
def source_lock(path):
    lock = path.with_name('.' + path.name + '.vllm-model-register.lock')
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError('指定 registry 已有注册锁，不清理其他任务的锁') from error
    identity = os.fstat(descriptor)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        current = lock.stat()
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            lock.unlink()


def apply_source(request, attempt):
    path = Path(request['target']['registry_path'])
    expected = request['target']['registry_sha256']
    with source_lock(path):
        raw = read_bytes(path)
        if digest(raw) != expected:
            raise ValueError('注册写入前源码身份已变化')
        changed = proposed_source(request, raw)
        write_json(attempt / 'registry-before.json', {'path': str(path), 'sha256': digest(raw), 'size': len(raw)})
        write_json(attempt / 'registry-after.json', {'path': str(path), 'sha256': digest(changed), 'size': len(changed)})
        difference = ''.join(difflib.unified_diff(raw.decode('utf-8-sig').splitlines(True),
            changed.decode('utf-8-sig').splitlines(True), fromfile=str(path), tofile=str(path), n=0))
        (attempt / 'registration.diff').write_text(difference)
        descriptor, temporary = tempfile.mkstemp(prefix='.register-source-', dir=path.parent)
        try:
            os.fchmod(descriptor, stat.S_IMODE(path.stat().st_mode))
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(changed)
            if digest(read_bytes(path)) != expected:
                raise ValueError('原子替换前源码身份已变化')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'action': 'source_registered', 'registry_sha256': digest(changed),
            'diff': str(attempt / 'registration.diff'), 'before': str(attempt / 'registry-before.json'),
            'after': str(attempt / 'registry-after.json')}
