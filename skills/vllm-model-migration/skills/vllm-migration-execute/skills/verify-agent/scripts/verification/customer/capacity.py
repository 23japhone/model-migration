"""采用明确架构/版本配方估算单精度请求容量，不猜测未知缓存布局。"""
import itertools
import math


def validate(spec, devices):
    for key in ('weight_bytes', 'kv_bytes_per_token', 'context_tokens', 'runtime_bytes_per_device'):
        if type(spec.get(key)) is not int or spec[key] <= 0:
            raise ValueError('容量配方缺少有效 ' + key)
    fraction = spec.get('memory_fraction')
    if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 < fraction < 1:
        raise ValueError('容量配方须明确显存预算比例')
    parallel = spec.get('allowed_tp')
    if not isinstance(parallel, list) or not parallel or any(type(n) is not int or n < 1 for n in parallel):
        raise ValueError('须有经模型和版本核验的 allowed_tp')
    device_records(devices)


def device_records(devices):
    ids = []
    for device in devices:
        for key in ('id', 'free_bytes', 'total_bytes'):
            if type(device.get(key)) is not int or device[key] < 0:
                raise ValueError('设备观察无效')
        if type(device.get('occupied')) is not bool or device['free_bytes'] > device['total_bytes']:
            raise ValueError('设备占用/显存观察无效')
        ids.append(device['id'])
    if len(set(ids)) != len(ids):
        raise ValueError('设备观察重复')


def fits(spec, selected):
    count = len(selected)
    # kv_bytes_per_token 是整个模型的总缓存；复制型 KV 配方需显式给出复制下限。
    replicated = spec.get('kv_replicated_bytes_per_token', 0)
    if type(replicated) is not int or replicated < 0:
        raise ValueError('KV 复制预算无效')
    per_token = max(math.ceil(spec['kv_bytes_per_token'] / count), replicated)
    weight = math.ceil(spec['weight_bytes'] / count)
    overhead = spec['runtime_bytes_per_device']
    budget = min(min(d['free_bytes'], int(d['total_bytes'] * spec['memory_fraction'])) for d in selected)
    capacity = max(0, (budget - weight - overhead) // per_token)
    return capacity >= spec['context_tokens'], capacity


def select(spec, devices, requested, *, policy=None):
    if policy is not None:
        from allocation_contract import POLICY
        from . import shared_capacity
        if policy != POLICY:
            raise ValueError('未知设备分配策略')
        return shared_capacity.select(spec, devices, requested)
    validate(spec, devices)
    free = sorted((d for d in devices if not d['occupied']), key=lambda d: d['id'])
    candidates = selections(spec, free, requested)
    for selected in candidates:
        valid, capacity = fits(spec, selected)
        if valid:
            return {'devices': [d['id'] for d in selected], 'tensor_parallel_size': len(selected),
                    'kv_token_capacity': capacity, 'context_tokens': spec['context_tokens'],
                    'memory_fraction': spec['memory_fraction']}
    raise ValueError('可用设备不满足已核验的 TP 与显存预算；不重试占用其他任务设备')


def selections(spec, free, requested):
    if isinstance(requested, list):
        if not requested or len(set(requested)) != len(requested):
            raise ValueError('显式设备列表无效')
        by_id = {d['id']: d for d in free}
        selected = [by_id[n] for n in requested if type(n) is int and n in by_id]
        if len(selected) != len(requested):
            raise ValueError('显式 NPU 不存在或已被占用，不自动替换')
        candidates = [selected] if len(selected) in spec['allowed_tp'] else []
    elif type(requested) is int and requested == -1:
        candidates = (list(group) for n in sorted(set(spec['allowed_tp'])) if n <= len(free)
                      for group in itertools.combinations(free, n))
    else:
        raise ValueError('设备须为列表或数字 -1')
    return candidates
