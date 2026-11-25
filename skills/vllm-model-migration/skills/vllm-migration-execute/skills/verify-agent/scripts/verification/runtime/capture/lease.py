"""跨 CLI 调用的持久租约；不自动清理来源不明或失活租约。"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess

from .adapters.state import read


@contextmanager
def locked(root):
    with (root / 'control.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def no_lease(root):
    if (root / 'lease.json').exists() or (root / 'active.json').exists():
        raise ValueError('已有采集租约或活动请求，保留现场，拒绝覆盖')


def identity(pid):
    os.kill(pid, 0)
    proc = Path('/proc', str(pid))
    if proc.exists():
        return {'pid': pid, 'namespace': os.readlink(proc / 'ns/pid'),
                'start': (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]}
    # 本机离线验收也绑定启动身份；实际 worker 仍由 Linux 载体核验。
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], check=True, capture_output=True, text=True)
    if not result.stdout.strip(): raise ValueError('采集控制进程已退出')
    return {'pid': pid, 'namespace': 'host', 'start': result.stdout.strip()}


def owned(root, authority, reference):
    current = read(root / 'lease.json')
    if current.get('session') != reference or current.get('nonce') != authority['nonce']:
        raise ValueError('租约身份不一致')
    owner = authority['owner']
    if owner['pid'] not in (os.getpid(), os.getppid()) or identity(owner['pid']) != owner:
        raise ValueError('租约控制进程或启动身份已变化')
    return current
