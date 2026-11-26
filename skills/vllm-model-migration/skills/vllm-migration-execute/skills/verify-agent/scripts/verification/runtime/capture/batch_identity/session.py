"""组租约共享控制权，每个成员独立封存。"""
from uuid import uuid4

from .... import request as schema
from ....evidence import write_json
from ..adapters.state import read
from ..adapters.batch.members import admission


def validate_arm(request, validate_single):
    joint = request['profile'] == 'batch_joint'
    schema.fields(request, ['operation', 'source', 'profile', 'requests'], ['cache_capture'] if joint else [])
    if 'cache_capture' in request:
        from ..session import validate_full_request
        schema.fields(request['cache_capture'], ['mode','max_request_snapshot_bytes'])
        validate_full_request(request)
    members = request['requests']
    if request['operation'] != 'arm' or not isinstance(members, list) or len(members) < 2:
        raise ValueError('批次身份采集须一次 arm 至少两个请求')
    traces = set()
    for item in members:
        schema.fields(item, ['trace_id', 'native_id', 'sample_key'])
        if not isinstance(item['trace_id'], str) or item['trace_id'] in traces:
            raise ValueError('组内 trace_id 必须是唯一字符串')
        traces.add(item['trace_id'])
        validate_single({'operation': 'arm', 'source': request['source'], **item})


def arm(request, root, lease, ready, preparation=None):
    if 'cache_capture' in request:
        from ..session import verify_arm
        verify_arm({'cache_capture': request['cache_capture']}, root, ready, preparation)
    if lease['state'] != 'idle' or (root / 'active.json').exists():
        raise ValueError('已有活动请求组或请求')
    profile = request['profile']
    if not ready.get(profile):
        raise ValueError(f'实际单卡 BF16 冷前缀同步 Eager worker 缺少 {profile} 能力')
    traces = [m['trace_id'] for m in request['requests']]
    if set(traces).intersection(item['trace_id'] for item in lease['sealed']):
        raise ValueError('会话中的 trace_id 已使用')
    group = root / uuid4().hex
    group.mkdir(mode=0o700)
    for item in request['requests']:
        folder = group / ('member-' + item['trace_id'])
        folder.mkdir(mode=0o700)
        write_json(folder / 'identity.json', item)
        write_json(folder / 'intent.json', {**item, 'request_id': item['trace_id'], 'session_mode': True})
    write_json(group / 'group.json', {'group_id': group.name, 'traces': traces, 'profile': request['profile'],
                                      **{k: request[k] for k in ('cache_capture',) if k in request}})
    lease.update(state='armed', group_id=group.name, pending=traces)
    write_json(root / 'lease.json', lease)
    write_json(root / 'active.json', {'group_id': group.name})
    return {'status': 'completed', 'group_id': group.name, 'trace_ids': traces}


def finish_member(root, lease, request, authority, handoff):
    from .receipt import seal
    trace = request['trace_id']
    if trace not in lease['pending']:
        raise ValueError('本组没有待封存的匹配成员')
    group = root / lease['group_id']
    folder = group / ('member-' + trace)
    if not (folder / 'frontend-end.json').is_file():
        raise ValueError('该成员的前端请求尚未结束')
    result = seal(group, folder, request, authority, handoff)
    lease['sealed'].append(result)
    lease['pending'].remove(trace)
    if not lease['pending']:
        lease.update(state='idle')
        del lease['group_id'], lease['pending']
        (root / 'active.json').unlink()
    write_json(root / 'lease.json', lease)
    return result


def close_group(root, lease):
    group = root / lease['group_id']
    folders = [group / ('member-' + name) for name in lease['pending']]
    with admission(group):
        if any((path / 'claimed').exists() and not (path / 'frontend-end.json').exists() for path in folders):
            raise ValueError('实际组成员尚未结束，保留活动租约')
        for folder in folders:
            write_json(folder / 'aborted.json', {'reason': '组成员未封存'})
        (root / 'active.json').unlink()
