"""从实测映射选择运行时逻辑编号；不接触硬件。"""
from device_contract import RT, VISIBLE, ids, require, rows


def creation_environment(request):
    env = dict(request['runtime']['env'])
    physical = request['npu']['devices']
    if VISIBLE in env:
        require(set(ids(env[VISIBLE])) == set(physical), '显式容器物理设备注入与选卡冲突')
    else:
        env[VISIBLE] = ','.join(map(str, physical))
    return env


def resolve(physical, observed, environment, ownership='owned'):
    require(bool(observed), 'CANN 未返回基础设备映射')
    by_physical = {row['physical_id']: row['logical_id'] for row in observed}
    require(len(by_physical) == len(observed) == len(set(by_physical.values())), '设备映射不是一一对应')
    require(set(physical) <= by_physical.keys(), '所选物理卡不在载体可见映射中')
    env = dict(environment)
    if RT in env:
        selected = ids(env[RT])
    elif ownership == 'owned':
        selected = [by_physical[device] for device in physical]
        env[RT] = ','.join(map(str, selected))
    else:
        selected = [row['logical_id'] for row in observed]
    reverse = {logical: device for device, logical in by_physical.items()}
    require(set(selected) <= reverse.keys(), '显式 RT 逻辑编号不在载体中')
    require({reverse[n] for n in selected} == set(physical), '运行时逻辑编号未对应选中的物理卡')
    result = [{'physical_id': reverse[n], 'logical_id': n, 'visible_index': i}
              for i, n in enumerate(selected)]
    return env, rows(result)


def validate_visible(expected, actual):
    require(rows(actual) == expected, '进程实际可见索引或物理卡与预期映射不一致')
