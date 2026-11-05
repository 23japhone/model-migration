"""先空闲、再共享的组合搜索；所有预算算法归公开静态合同。"""
import itertools

from allocation_contract import CapacityBlocked, budget, validate
from device_contract import require


def candidates(spec, devices, requested):
    by_id = {d['id']: d for d in devices}
    if isinstance(requested, list):
        require(bool(requested) and all(type(n) is int and n in by_id for n in requested)
                and len(set(requested)) == len(requested), '显式 NPU 不存在或列表无效，不自动替换')
        return [[by_id[n] for n in requested]] if len(requested) in spec['allowed_tp'] else []
    require(type(requested) is int and requested == -1, '设备须为列表或数字 -1')
    ordered = sorted(devices, key=lambda d: d['id'])
    groups = [list(group) for n in sorted(set(spec['allowed_tp'])) if n <= len(ordered)
              for group in itertools.combinations(ordered, n)]
    return sorted(groups, key=lambda group: (sum(d['occupied'] for d in group), len(group), [d['id'] for d in group]))


def select(spec, devices, requested):
    validate(spec, devices)
    groups = candidates(spec, devices, requested)
    if not groups:
        raise CapacityBlocked('tp_unavailable', '允许设备范围内没有配方支持的 TP 组合',
                              allowed_tp=spec['allowed_tp'], devices=devices)
    attempted = []
    for selected in groups:
        result = budget(spec, selected)
        if result['memory_fraction'] > 0 and result['kv_token_capacity'] >= spec['context_tokens']:
            return result
        result['selection_reason'] = '该组合逐卡容量预算不足'
        attempted.append(result)
    raise CapacityBlocked('capacity_insufficient', '允许卡的剩余显存不足，请更换其他卡或释放显存后继续',
                          devices=devices, allowed_tp=spec['allowed_tp'], candidates=attempted)
