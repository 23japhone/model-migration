"""Linux目标解释器中的短任务：subreaper绑定派生树，跨Docker客户端取消。"""
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


def record(pid):
    fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'ppid': int(fields[1]), 'start_ticks': fields[19], 'state': fields[0]}


def same(value):
    try:return record(value['pid'])['start_ticks'] == value['start_ticks']
    except (OSError, ValueError):return False


def descendants(root):
    records = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            value = record(int(path.parent.name));records[value['pid']] = value
        except (OSError, ValueError):continue
    result = [];frontier = {root}
    while frontier:
        found = [v for v in records.values() if v['ppid'] in frontier and v['pid'] != root]
        result.extend(found);frontier = {v['pid'] for v in found}
    return [v for v in result if v['state'] != 'Z']


def clear_tree(root):
    for number in range(4):
        children = descendants(root)
        if not children:return
        for value in reversed(children):
            if not same(value):continue
            try:os.kill(value['pid'], signal.SIGTERM if number == 0 else signal.SIGKILL)
            except ProcessLookupError:pass
        time.sleep(.15)
    if descendants(root):raise RuntimeError('目标任务仍有可执行派生进程')


def reap():
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:return
        except ChildProcessError:return


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    os.replace(temporary, path)


def subreaper():
    library = ctypes.CDLL(None, use_errno=True)
    function = library.prctl
    function.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    if function(36, 1, 0, 0, 0) != 0:raise OSError('无法设置Linux子进程收养边界')
    if os.getpgrp() != os.getpid():os.setsid()


def interrupted(signum, frame):
    raise KeyboardInterrupt('目标任务被取消')


def execute(path, marker, argv):
    subreaper()
    signal.signal(signal.SIGTERM, interrupted)
    meta = {**record(os.getpid()), 'status': 'running'}
    save(path, meta)
    child = None;stdout = b'';stderr = b'';code = 1
    try:
        if marker.exists():raise KeyboardInterrupt('目标启动前已取消')
        data = sys.stdin.buffer.read()
        if marker.exists():raise KeyboardInterrupt('目标启动前已取消')
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = child.communicate(data, timeout=900)
        code = child.returncode
        meta['status'] = 'completed' if code == 0 else 'failed'
    except (KeyboardInterrupt, subprocess.TimeoutExpired) as error:
        meta.update(status='cancelled', diagnostic=type(error).__name__)
        code = 130
    finally:
        clear_tree(os.getpid())
        if child is not None:
            tail_out, tail_err = child.communicate(timeout=3)
            if not stdout:stdout = tail_out
            if not stderr:stderr = tail_err
        reap()
        meta['remaining'] = descendants(os.getpid())
        save(path, meta)
        sys.stdout.buffer.write(stdout);sys.stderr.buffer.write(stderr)
        sys.stderr.write('\nVLLM_VERIFY_JOB=' + json.dumps(meta) + '\n')
    return code


def cancel(path, marker):
    marker.touch(exist_ok=True)
    deadline = time.monotonic() + 2
    while not path.exists() and time.monotonic() < deadline:time.sleep(.05)
    if not path.exists():return {'status': 'cancel_requested_before_start'}
    meta = json.loads(path.read_text())
    if meta['status'] in ('completed', 'failed', 'cancelled') and not same(meta):return meta
    if not same(meta):raise ValueError('目标任务PID启动身份已变化，拒绝清理')
    clear_tree(meta['pid'])
    if same(meta):
        os.kill(meta['pid'], signal.SIGTERM)
        time.sleep(.15)
        if same(meta):os.kill(meta['pid'], signal.SIGKILL)
    meta.update(status='cancelled', remaining=[])
    save(path, meta)
    return meta


def main():
    mode, job_id = sys.argv[1:3]
    if not re.fullmatch('[a-f0-9]{32}', job_id):raise ValueError('目标任务标识无效')
    path = Path('/tmp/vllm-verify-exec-' + job_id + '.json')
    marker = path.with_suffix('.cancel')
    if mode == 'cancel':
        print(json.dumps(cancel(path, marker)));return 0
    if mode != 'run':raise ValueError('目标任务操作无效')
    return execute(path, marker, sys.argv[3:])


if __name__ == '__main__':raise SystemExit(main())
