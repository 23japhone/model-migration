"""绑定准备回执中的卡映射、实际 TP 成员和 executor 输出角色。"""
from ..adapters.parallel import EXECUTOR_SHA


def validate_members(topology, records):
    members = [records[f'worker-rank{i}'] for i in range(2)]
    devices = [(d['physical_id'], d['visible_index']) for d in topology['devices']]
    observed = [(m['physical_id'], m['visible_index']) for m in members]
    if len(set(devices)) != 2 or sorted(devices) != sorted(observed):
        raise ValueError('两个 worker 实际设备与声明映射不符')
    if len({(m['namespace'], m['pid']) for m in members}) != 2:
        raise ValueError('TP worker 进程身份重复')
    for rank, member in enumerate(members):
        if (member['rank'], member['tp_rank'], member['world_size'], member['group_ranks']) != (rank, rank, 2, [0, 1]):
            raise ValueError('TP 组成员身份不符')
        if member['layers'] != members[0]['layers'] or member['architecture'] != members[0]['architecture']:
            raise ValueError('两个 TP worker 的模型结构不符')
    executor = records['executor']
    if (executor['world_size'], executor['output_rank'], executor['sha256']) != (2, 0, EXECUTOR_SHA):
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


def seal_members(folder, refs, inspect):
    from ....evidence import read_bound
    from pathlib import Path
    for i, ref in enumerate(refs):
        if Path(ref['path']) != folder / f'member-rank{i}.json':
            raise ValueError('租约引用了其他请求的 worker')
    members = [read_bound(ref) for ref in refs]
    errors = []
    try:
        current = inspect()
        if members != current['rank_workers']:
            errors.append('arm 与 seal 的 TP worker 身份不同')
    except (ValueError, OSError, RuntimeError) as error:
        errors.append('封存时服务身份检查失败 ' + str(error))
    return {**members[0], 'rank_workers': members, 'worker_refs': refs, 'output_rank': 0,
            'service_errors': errors}



def device_identity(member):
    fields = ('rank', 'tp_rank', 'world_size', 'physical_id', 'logical_id', 'visible_index',
              'pid', 'host_pid', 'namespace', 'start_ticks', 'group_ranks', 'device_backend')
    return {k: member[k] for k in fields if k in member}
