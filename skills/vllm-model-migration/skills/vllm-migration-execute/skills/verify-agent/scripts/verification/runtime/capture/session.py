"""正式 benchmark 请求的 begin/arm/seal/end；此入口不发送推理请求。"""
import os
from pathlib import Path
import re
import uuid

from ... import outputs, request as schema
from ...evidence import read_bound, write_json
from .adapters.state import read
from .lease import identity, locked, no_lease, owned
from .service import deployment, live
from .tensors import file_ref


def validate(request):
    schema.fields(request, ['operation', 'source'], ['owner_pid', 'sample_key', 'native_id', 'trace_id', 'http'])
    schema.fields(request['source'], ['path', 'sha256']); schema.absolute(request['source']['path'])
    if not schema.HASH.fullmatch(str(request['source']['sha256'])): raise ValueError('来源摘要无效')
    op = request['operation']
    permitted = {'begin': {'owner_pid'}, 'arm': {'sample_key', 'native_id', 'trace_id'},
                 'seal': {'trace_id', 'http'}, 'end': set(), 'status': set()}
    if op not in permitted or set(request) - {'operation', 'source'} != permitted[op]:
        raise ValueError('采集会话操作字段不一致')
    if op == 'begin' and (type(request['owner_pid']) is not int or request['owner_pid'] <= 0):
        raise ValueError('owner_pid 无效')
    validate_operation(request)


def validate_operation(request):
    op = request['operation']
    if op in ('arm', 'seal') and not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}', str(request['trace_id'])):
        raise ValueError('trace_id 无效')
    if op == 'arm' and (not isinstance(request['sample_key'], str) or not request['sample_key'] or
                        not isinstance(request['native_id'], (str, int)) or isinstance(request['native_id'], bool)):
        raise ValueError('必须提供原生样本身份')
    if op == 'seal':
        schema.fields(request['http'], ['request', 'response'])
        for ref in request['http'].values():
            schema.fields(ref, ['path', 'sha256']); schema.absolute(ref['path'])
            if not schema.HASH.fullmatch(str(ref['sha256'])): raise ValueError('HTTP 原件摘要无效')


def configure(root, prep):
    native = {**read_bound(prep['config']), 'dump_path': str(Path(prep['dump']['runtime_path']) / 'native'), 'dump_enable': True}
    path = root / 'capture-config.json'
    if path.exists() and read(path) != native: raise ValueError('派生采集配置冲突')
    if not path.exists(): write_json(path, native)
    return file_ref(path, root)


def begin(request, target, run_id):
    declared, handoff, prep = deployment(request['source'])
    outputs.check(target, outputs.protected_roots(declared))
    root = Path(prep['config']['path']).parent
    with locked(root):
        no_lease(root)
        owner = identity(request['owner_pid'])
        if owner['pid'] not in (os.getpid(), os.getppid()): raise ValueError('控制进程不是调用方')
        ready = live(handoff, prep, target)
        if ready.get('coverage_capability') != 'modules_and_registered_apis':
            raise ValueError('旧 worker 缺少完整调用清单能力，请使用新适配器部署')
        folder = target / uuid.uuid4().hex; folder.mkdir(parents=True, exist_ok=False)
        authority = {'schema_version': '1.0', 'source': request['source'], 'root': str(root), 'owner': owner,
                     'nonce': uuid.uuid4().hex, 'run_id': run_id, 'derived_config': configure(root, prep)}
        path = folder / 'capture-session.json'; write_json(path, authority); reference = file_ref(path, folder)
        write_json(root / 'lease.json', {'session': reference, 'nonce': authority['nonce'], 'state': 'idle', 'sealed': []})
        return 0, {'status': 'completed', 'session': reference}


def arm(request, root, lease, ready):
    if lease['state'] != 'idle' or (root / 'active.json').exists(): raise ValueError('已有活动请求')
    if any(row['trace_id'] == request['trace_id'] for row in lease['sealed']): raise ValueError('trace_id 已使用')
    folder = root / ('request-' + uuid.uuid4().hex); folder.mkdir(mode=0o700)
    write_json(folder / 'intent.json', {**request, 'request_id': request['trace_id'], 'session_mode': True})
    lease.update(state='armed', directory=folder.name, trace_id=request['trace_id'])
    write_json(root / 'lease.json', lease)
    write_json(root / 'active.json', {'directory': folder.name})
    return {'status': 'completed', 'directory': folder.name, 'trace_id': request['trace_id']}


def operate(request, target):
    authority = read_bound(request['source']); root = Path(authority['root'])
    with locked(root):
        lease = owned(root, authority, request['source'])
        declared, handoff, prep = deployment(authority['source'])
        read_bound(authority['derived_config'])
        if request['operation'] == 'end':
            if lease['state'] == 'armed':
                folder = root / lease['directory']
                if (folder/'request.json').exists() and not (folder/'frontend-end.json').exists():
                    raise ValueError('实际请求尚未结束，保留活动租约；不能让后续采集混入该前向')
                write_json(folder / 'aborted.json', {'reason': '会话结束时请求未完整封存', 'trace_id': lease['trace_id']})
                (root / 'active.json').unlink(missing_ok=True)
            write_json(Path(request['source']['path']).parent / 'session-end.json', lease)
            (root / 'lease.json').unlink()
            return {'status': 'completed', 'sealed': lease['sealed'], 'previous_state': lease['state']}
        ready = live(handoff, prep, target)
        if request['operation'] == 'arm': return arm(request, root, lease, ready)
        if request['operation'] == 'seal':
            from .session_receipt import seal
            result = seal(request, authority, handoff, root, lease, ready)
            lease['sealed'].append(result); lease.update(state='idle')
            write_json(root / 'lease.json', lease)
            (root / 'active.json').unlink()
            return result
        return {'status': 'completed', 'state': lease['state'], 'sealed': lease['sealed']}


def run(request, target, run_id):
    validate(request)
    if request['operation'] == 'begin': return begin(request, target, run_id)
    return 0, operate(request, target)
