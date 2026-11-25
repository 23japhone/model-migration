"""绑定准备回执中的卡映射、实际 TP 成员和 executor 输出角色。"""
from ..adapters.parallel import EXECUTOR_SHA


def group_size(topology):
    devices = topology['devices']
    size = len(devices)
    declared = topology.get('world_size', size)
    if type(declared) is not int or declared != size or size < 2:
        raise ValueError('TP 进程组大小与声明设备数不符')
    if (len({d['physical_id'] for d in devices}) != size
            or {d['visible_index'] for d in devices} != set(range(size))):
        raise ValueError('物理卡与可见设备映射不完整或重复')
    return size


def expected_worker_roles(topology, observed):
    roles = [f'worker-rank{i}' for i in range(group_size(topology))]
    mismatched = set(roles).symmetric_difference(observed)
    if mismatched:
        raise ValueError('TP worker 就绪记录与预期成员不符 ' + str(sorted(mismatched)))
    return roles


def validate_members(topology, records):
    roles = expected_worker_roles(topology, (name for name in records if name.startswith('worker-rank')))
    size = len(roles)
    ranks = list(range(size))
    members = [records[role] for role in roles]
    devices = [(d['physical_id'], d['visible_index']) for d in topology['devices']]
    observed = [(m['physical_id'], m['visible_index']) for m in members]
    if sorted(devices) != sorted(observed):
        raise ValueError('TP worker 实际设备与声明映射不符')
    if len({(m['namespace'], m['pid']) for m in members}) != size:
        raise ValueError('TP worker 进程身份重复')
    for rank, member in enumerate(members):
        if (member['rank'], member['tp_rank'], member['world_size'], member['group_ranks']) != (rank, rank, size, ranks):
            raise ValueError(f'rank{rank} 的 TP 组成员身份不符')
        if (member['layers'], member['architecture']) != (members[0]['layers'], members[0]['architecture']):
            raise ValueError(f'rank{rank} 的模型结构不同')
    executor = records['executor']
    if (executor['world_size'], executor['output_rank'], executor['sha256']) != (size, 0, EXECUTOR_SHA):
        raise ValueError('实际 executor 或输出 rank 未核实')
    return {**members[0], 'rank_workers': members, 'output_rank': executor['output_rank']}


def bind_at_arm(folder, ready):
    from ....evidence import write_json
    from ..tensors import file_ref
    refs = []
    for member in ready['rank_workers']:
        path = folder / f"member-rank{member['rank']}.json"
        write_json(path, member)
        refs.append(file_ref(path, folder))
    return refs


def arm_member(folder, rank, size, ref):
    from pathlib import Path
    from ....evidence import read_bound

    if Path(ref['path']) != folder / f'member-rank{rank}.json':
        raise ValueError('租约引用了其他请求或 rank 的 worker')
    try:
        worker = read_bound(ref)
        if (worker['rank'], worker['world_size']) != (rank, size):
            raise ValueError('进程身份与绑定的 rank 或 TP 大小不符')
        return worker
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'rank': rank, 'world_size': size, 'layers': [],
                'binding_error': f'rank{rank} 缺少可信的 arm 身份: {error}'}


def seal_members(folder, refs, inspect, *, size=None):
    if size is None:
        size = len(refs)
    if len(refs) != size or size < 2:
        raise ValueError('租约绑定的成员数与准备拓扑不符')
    members = [arm_member(folder, rank, size, ref) for rank, ref in enumerate(refs)]
    errors = [worker['binding_error'] for worker in members if 'binding_error' in worker]
    try:
        current = inspect()['rank_workers']
        if len(current) != size:
            errors.append('seal 时的 TP 成员数不同')
        for worker, live in zip(members, current):
            if 'binding_error' not in worker and worker != live:
                errors.append(f"rank{worker['rank']} 在 arm 与 seal 的身份不同")
    except (ValueError, OSError, RuntimeError) as error:
        errors.append('封存时服务身份检查失败 ' + str(error))
    return {**members[0], 'rank_workers': members, 'worker_refs': refs, 'output_rank': 0,
            'service_errors': errors}


def device_identity(member):
    fields = ('rank', 'tp_rank', 'world_size', 'physical_id', 'logical_id', 'visible_index',
              'pid', 'host_pid', 'namespace', 'start_ticks', 'group_ranks', 'device_backend')
    return {k: member[k] for k in fields if k in member}
