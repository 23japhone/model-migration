"""目标进程文件协议；每个控制目录只对应一个采集请求。"""
import hashlib
import json
import os
from pathlib import Path
import uuid


def root():
    return Path(os.environ['VERIFY_EAGER_ROOT'])


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, allow_nan=False, ensure_ascii=False))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def active():
    try:
        active_record = read(root() / 'active.json')
        return None if 'group_id' in active_record else root() / active_record['directory']
    except FileNotFoundError:
        return None


def mapped():
    folder = active()
    if folder:
        try:
            return folder, read(folder / 'request.json')['internal_id']
        except FileNotFoundError:
            pass
    return None, None


def ready(role, **values):
    src = Path(__file__).parent
    files = {p.relative_to(src).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob('*.py')}
    write(root() / (role + '.json'), {**values, 'adapter_files': files, 'pid': os.getpid(),
          'namespace': os.readlink('/proc/self/ns/pid'),
          'start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]})


def fail(folder, error):
    if folder:
        write(folder / ('error-' + uuid.uuid4().hex + '.json'), {'error': str(error), 'pid': os.getpid()})
