"""共享分配的历史证据复核与启动前最新容量门禁。"""
import math

import allocation_contract
from allocation_contract import CapacityBlocked
from device_contract import require
from ..customer import npu_capacity
from ..evidence import write_json


def validated(request):
    loaded = allocation_contract.load(request)
    if loaded is None:
        return None
    inputs, result, observed = loaded
    # 历史证据在运行复核时不要求60秒内，但原文、启动主机及卡状态仍须一致。
    npu_capacity.bound_devices(inputs['observation'], fresh=False)
    return result, observed


def shared(request):
    loaded = validated(request)
    return loaded is not None and loaded[0]['sharing_mode'] == 'shared'


def current_budgets(result, observed):
    by_id = {d['id']: d for d in observed['devices']}
    rows = []
    for planned in result['budgets']:
        current = by_id.get(planned['id'])
        require(current is not None, '最新容量观察缺少选中设备')
        require(current['total_bytes'] == planned['total_bytes'], '选中设备总容量变化')
        if result['sharing_mode'] == 'exclusive' and current['occupied']:
            raise CapacityBlocked('resources_changed', '原空闲卡出现其他进程，请重新观察并规划，不能沿用独占分配')
        reserve = (current['free_bytes'] + 19) // 20 if result['sharing_mode'] == 'shared' else 0
        required = math.ceil(current['total_bytes'] * result['memory_fraction']) + reserve
        rows.append({**current, 'reserve_bytes': reserve, 'planned_budget_bytes': planned['budget_bytes'],
                     'required_free_bytes': required, 'shortfall_bytes': max(0, required - current['free_bytes'])})
    return rows


def before_start(request, attempt, label):
    loaded = validated(request)
    if loaded is None:
        return False
    result, original = loaded
    folder = attempt / ('allocation-' + label)
    folder.mkdir()
    observed = npu_capacity.observe(folder)
    write_json(folder / 'observation.json', {'status': 'completed', 'operation': 'observe', 'result': observed})
    require(observed['host_identity'] == original['host_identity'], '启动前容量观察主机身份变化')
    rows = current_budgets(result, observed)
    write_json(folder / 'budget-check.json', {'allocation': request['npu']['allocation'], 'budgets': rows})
    if any(row['shortfall_bytes'] for row in rows):
        raise CapacityBlocked('capacity_insufficient', '启动前剩余显存不足以满足已确定预算，请更换其他卡或释放显存',
                              stage=label, devices=result['devices'], budgets=rows)
    return True
