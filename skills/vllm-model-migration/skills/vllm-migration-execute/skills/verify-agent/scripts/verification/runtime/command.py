"""短命令的有界执行；主进程退出不等于派生进程已结束。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from ..evidence import write_json


def active_group(pgid):
    if Path('/proc/self/stat').exists():
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == pgid and fields[0] != 'Z':return True
            except (OSError, ValueError):continue
        return False
    rows = subprocess.check_output(['ps', '-axo', 'pgid=,stat='], text=True, timeout=2)
    return any(int(row.split()[0]) == pgid and not row.split()[1].startswith('Z')
               for row in rows.splitlines() if row.strip())


def kill_group(pgid):
    if not active_group(pgid):return
    try:os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:return
    time.sleep(0.2)
    if not active_group(pgid):return
    try:os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:pass


def run(argv, log_base, timeout=120, input_text=None, env=None, cwd=None, cancel=None):
    base = Path(log_base)
    write_json(str(base) + '.command.json', argv)
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, env=env, cwd=cwd)
    outcome = {'pid': process.pid, 'status': 'running'}
    try:
        write_json(str(base) + '.process.json', outcome)
        stdout, stderr = process.communicate(input_text, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt, OSError) as error:
        try:
            if cancel:cancel()
        except (OSError, RuntimeError, ValueError) as cleanup_error:
            outcome['cleanup_error'] = str(cleanup_error)
        finally:kill_group(process.pid)
        stdout, stderr = process.communicate(timeout=3)
        outcome.update(status='cancelled' if isinstance(error, KeyboardInterrupt) else
                       ('failed' if isinstance(error, OSError) else 'timeout'))
        (base.parent / (base.name + '.stdout.log')).write_text(stdout)
        (base.parent / (base.name + '.stderr.log')).write_text(stderr)
        write_json(str(base) + '.process.json', outcome)
        if outcome.get('cleanup_error'):
            raise RuntimeError('目标内部清理未确认，详见进程证据') from error
        if isinstance(error, (KeyboardInterrupt, OSError)):raise
        raise TimeoutError('命令超时，已结束本次进程组') from error
    outcome.update(status='completed' if process.returncode == 0 else 'failed', returncode=process.returncode)
    (base.parent / (base.name + '.stdout.log')).write_text(stdout)
    (base.parent / (base.name + '.stderr.log')).write_text(stderr)
    write_json(str(base) + '.process.json', outcome)
    if process.returncode:
        if cancel:cancel()
        raise RuntimeError('命令执行失败，详见独立 stderr 证据')
    return stdout
