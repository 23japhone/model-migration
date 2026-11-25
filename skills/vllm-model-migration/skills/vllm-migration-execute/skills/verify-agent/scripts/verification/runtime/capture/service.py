"""校验第一步部署交接、运行载体及本次进程就绪记录。"""
import os
from pathlib import Path
from urllib.parse import urlsplit

from device_contract import load as device_contract

from ...evidence import read_bound, sha256
from ...review import review_source
from .. import carrier, inspection
from .adapters import state
from .adapters.sources import SOURCES
from .parallel.topology import expected_worker_roles, validate_members


def deployment(source_ref):
    source = read_bound(source_ref)
    review_source(source)
    details = source['detailed_report']
    handoff = read_bound({'path': source['evidence']['handoff'], 'sha256': details['handoff_sha256']})
    if handoff['resource']['ownership'] != 'owned' or handoff['final_service']['state'] != 'retained':
        raise ValueError('采集需要保持在线的自有部署')
    if any(source['conclusions'].get(k) != 'pass' for k in ('workers', 'lifecycle', 'functional_protocol')):
        raise ValueError('部署报告缺少功能、worker 或生命周期通过证据')
    declared, _, _ = device_contract(handoff, source['inputs'])
    if handoff['endpoint'] != declared['service']['endpoint']:
        raise ValueError('部署报告、设备声明与服务地址身份不一致')
    capture = handoff.get('worker_capture', {})
    if capture != details.get('worker_capture') or capture.get('status') != 'prepared':
        raise ValueError('部署未交接 prepared 的 worker_capture')
    preparation = read_bound(capture['receipt'])
    if (preparation['run_id'] != source['run_id'] or preparation['status'] != 'prepared'
            or preparation['attempt_id'] != Path(source_ref['path']).parent.name):
        raise ValueError('采集准备回执的尝试身份不一致')
    config = preparation['config']
    read_bound(config)
    return source['inputs'], handoff, preparation


def process_matches(path, record):
    try:
        ns = os.readlink(path / 'ns/pid')
        ticks = (path / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        pid = next(x for x in (path / 'status').read_text().splitlines() if x.startswith('NSpid:')).split()[-1]
        return ns == record['namespace'] and ticks == record['start_ticks'] and int(pid) == record['pid']
    except (FileNotFoundError, ProcessLookupError, PermissionError, StopIteration):
        return False


def live(handoff, preparation, attempt):
    carrier.validate(handoff['resource'], attempt)
    workers = handoff['workers']
    inspection.confirm_workers(workers, handoff['resource'])
    owners = inspection.listener_owners(urlsplit(handoff['endpoint']).port, handoff['resource'])
    if sorted(owners) != sorted(workers['listener_pids']):
        raise ValueError('服务监听者已变化')
    root = Path(preparation['config']['path']).parent
    errors = list(root.glob('error-*.json'))
    if errors:
        raise ValueError(state.read(errors[0])['error'])
    topology = read_bound(preparation['parallel']) if 'parallel' in preparation else None
    if topology is not None:
        worker_roles = expected_worker_roles(topology, (p.stem for p in root.glob('worker-rank*.json')))
        roles = [*worker_roles, 'engine', 'executor', 'frontend']
    else:
        worker_roles = ['worker']
        roles = [*worker_roles, 'engine']
        if state.read(root/'worker.json').get('coverage_capability'):
            roles.append('frontend')
    identities = {role: verified_role(root, handoff, preparation, role, worker_roles) for role in roles}
    verify_sources(root, preparation)
    read_bound(preparation['config'])
    if topology is not None:
        return validate_members(topology, identities)
    return state.read(root / 'worker.json')


def verify_sources(root, preparation):
    for name, digest in preparation['adapter_files'].items():
        if sha256(root / 'adapters' / name) != digest:
            raise ValueError('已部署的适配器源码变化 ' + name)
    allowed_sources = SOURCES
    if preparation.get('graph'):
        from .adapters.aclgraph.hooks import MODULE, DIGEST
        if preparation['probe']['graph'].get(MODULE) != DIGEST or state.read(root/'graph.json') != preparation['graph']:
            raise ValueError('图模式准备配置或 wrapper 身份不同')
        if state.read(root/'worker.json')['graph'] != preparation['graph']:
            raise ValueError('实际图 worker 配置与部署不同')
        allowed_sources = {**SOURCES, MODULE: (DIGEST,)}
    for name, allowed in allowed_sources.items():
        if state.read(root / ('hook-' + name + '.json'))['sha256'] not in allowed:
            raise ValueError('目标源码身份不符 ' + name)



def verified_role(root, handoff, preparation, role, worker_roles):
    path = root / (role + '.json')
    if not path.exists():
        raise ValueError(f'准备完成，实际 {role} 缺少就绪记录')
    record = state.read(path)
    if record['adapter_files'] != preparation.get('adapter_files'):
        raise ValueError(f'{role} 适配器文件摘要不同')
    if role in worker_roles:
        processes = handoff['workers']['npu_processes']
        if 'physical_id' in record:
            processes = [row for row in processes if row['device'] == record['physical_id']]
        paths = {Path('/proc', str(row['pid'])) for row in processes}
    else:
        paths = Path('/proc').glob('[0-9]*')
    found = [path for path in paths if process_matches(path, record)]
    if len(found) != 1 or not inspection.same_owner(int(found[0].name), handoff['resource']):
        raise ValueError(f'{role} 的部署身份已变化')
    if role.startswith('worker-rank'):
        record['host_pid'] = int(found[0].name)
    return record
