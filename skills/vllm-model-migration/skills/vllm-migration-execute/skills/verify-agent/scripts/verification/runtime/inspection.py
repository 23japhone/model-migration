"""实际载体内容、监听socket和物理NPU进程的归属证据。"""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .command import run
from .execution import execute
from .process import identity as process_identity
from ..evidence import write_json
from ..devices import inventory
from ..devices import allocation
from ..devices.workers import select_workers, verify_library


def runtime_identity(request, resource, prefix, attempt):
    files = dict(request['runtime']['files'])
    model = request['model']
    files.update({str(Path(model['runtime_root']) / p): sha for p, sha in model['files'].items()})
    expected = {'files': files, 'versions': request['runtime']['versions']}
    code = Path(__file__).with_name('probe.py').read_text()
    env = {**os.environ, **request['runtime']['env']}
    raw = execute(prefix, ['-B', '-c', code], attempt / 'runtime-probe', timeout=900,
              input_text=json.dumps(expected), env=env, cwd=request['runtime'].get('cwd') if request['runtime']['kind'] == 'process' else None)
    lines = [line.removeprefix('VLLM_VERIFY_JSON=') for line in raw.splitlines() if line.startswith('VLLM_VERIFY_JSON=')]
    if len(lines) != 1:raise ValueError('载体采集没有唯一有效回执')
    result = json.loads(lines[0])
    write_json(attempt / 'runtime-identity.json', result)
    if result['versions'] != expected['versions'] or not all(v['match'] for v in result['files'].values()):
        raise ValueError('最终运行载体版本或源码/资产内容身份不一致')
    if result['npu_count'] != len(request['npu']['devices']):
        raise ValueError('载体实际可见NPU数与所选设备不一致')
    return result


def same_owner(pid, resource):
    before = process_identity(pid)
    if resource['kind'] == 'docker':
        groups = Path('/proc', str(pid), 'cgroup').read_text()
        owner = resource['identity']['container_id'] in re.findall(r'(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])', groups)
    else:
        owner = before['pgid'] == resource['identity']['pgid']
    if owner and before != process_identity(pid):raise ValueError('检查归属时 PID 启动身份变化')
    return owner


def available(request, attempt):
    if allocation.before_start(request, attempt, 'before-create'):
        return
    occupied = [p for p in inventory.snapshot(attempt, 'npu-before') if p['device'] in request['npu']['devices']]
    if occupied:raise ValueError('所选物理卡有计算进程，不能清理其他任务或直接启动')


def listener_inodes(port):
    result = set()
    for name in ['tcp', 'tcp6']:
        for line in Path('/proc/net', name).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port:result.add(fields[9])
    return result


def listener_owners(port, resource):
    inodes = listener_inodes(port)
    owners = set()
    for fd in Path('/proc').glob('[0-9]*/fd/*'):
        try:
            target = os.readlink(fd)
            if target.startswith('socket:[') and target[8:-1] in inodes:owners.add(int(fd.parts[2]))
        except (OSError, ValueError):continue
    if not owners or not all(same_owner(pid, resource) for pid in owners):
        raise ValueError('端口监听进程不属于本次已核对的载体')
    return sorted(owners)


def running_evidence(request, resource, attempt):
    listeners = listener_owners(urlsplit(request['service']['endpoint']).port, resource)
    observed = inventory.snapshot(attempt, 'npu-after')
    selected = select_workers(observed, request['npu']['devices'], lambda pid: same_owner(pid, resource),
                              shared=allocation.shared(request))
    foreign = [row for row in observed if row['device'] in request['npu']['devices'] and row not in selected]
    for value in selected:
        if not same_owner(value['pid'], resource):raise ValueError('所选卡存在其他任务的NPU进程')
        before = process_identity(value['pid'])
        path = Path('/proc', str(value['pid']))
        value.update(identity=before, cgroup=(path / 'cgroup').read_text(), stat=(path / 'stat').read_text(),
                     cmdline=(path / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'))
        verify_library(value['pid'], request['_device_state']['library'])
        if before != process_identity(value['pid']):raise ValueError('读取 worker 证据时 PID 身份变化')
    expected = request['_device_state']['executable_realpath']
    executables = {str(pid): str(Path('/proc', str(pid), 'exe').resolve())
                   for pid in {*listeners, *(v['pid'] for v in selected)}}
    if any(path != expected for path in executables.values()):
        raise ValueError('实际API/worker解释器与已核对载体解释器不一致')
    result = {'listener_pids': listeners, 'listener_identities': [process_identity(pid) for pid in listeners],
              'npu_processes': selected, 'external_npu_processes': foreign, 'executables': executables}
    write_json(attempt / 'worker-identity.json', result)
    return result


def confirm_workers(workers, resource):
    identities = [*workers['listener_identities'], *(row['identity'] for row in workers['npu_processes'])]
    for expected in identities:
        if process_identity(expected['pid']) != expected:
            raise ValueError('设备观察结束时 PID 启动身份变化')
        if not same_owner(expected['pid'], resource):
            raise ValueError('设备观察结束时 worker 归属变化')
