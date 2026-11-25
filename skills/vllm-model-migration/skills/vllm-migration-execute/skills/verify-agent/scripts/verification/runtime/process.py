"""Linux PID/starttime/进程组身份；精确停止自有进程组。"""
import os
from pathlib import Path
import signal
import subprocess
import time

from .command import kill_group
from .capture.launch import command as launch_command
from ..evidence import write_json


def identity(pid):
    # comm可能有空格或括号，只有末尾右括号之后才按字段位置解析。
    value = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'start_ticks': value[19], 'pgid': int(value[2])}


def assert_identity(expected):
    actual = identity(expected['pid'])
    if any(actual[k] != expected[k] for k in ('pid', 'start_ticks', 'pgid')):
        raise ValueError('PID 或启动身份变化，拒绝操作')
    return actual


def create(request, attempt, nonce):
    if not Path('/proc/self/stat').exists():raise ValueError('process 生命周期要求 Linux /proc')
    return {'kind': 'process', 'ownership': 'owned', 'nonce': nonce, 'identity': None}


def launch(request, resource, attempt):
    log = attempt / 'service.log'
    env = {**os.environ, **request['runtime']['env']}
    with log.open('ab') as stream:
        process = subprocess.Popen(launch_command(request), stdout=stream, stderr=stream,
                                   stdin=subprocess.DEVNULL, start_new_session=True, env=env,
                                   cwd=request['runtime'].get('cwd'))
    resource['identity'] = identity(process.pid)
    resource['log_path'] = str(log)
    write_json(attempt / 'resource-pending.json', resource)


def members(pgid):
    found = []
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            data = identity(int(path.parent.name))
            if data['pgid'] == pgid:found.append(data)
        except (OSError, ValueError):continue
    return found


def stop(resource, attempt):
    expected = resource['identity']
    if not expected:return {'state': 'not_started'}
    try:assert_identity(expected)
    except FileNotFoundError:
        # 长期服务的组号可能已复用；启动时间较晚不能证明仍属于原服务。
        if members(expected['pgid']):
            raise ValueError('主进程已退出，无法证明现存进程组归属，拒绝清理')
        return {'state': 'stopped', 'identity': expected}
    kill_group(expected['pgid'])
    time.sleep(.1)
    active = []
    for member in members(expected['pgid']):
        stat = Path('/proc', str(member['pid']), 'stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] != 'Z':active.append(member)
    if active:raise RuntimeError('仍有可执行的自有派生进程')
    return {'state': 'stopped', 'identity': expected}
