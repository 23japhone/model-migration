"""共享 NPU 的静态预算及分配证据合同；不观察设备或操作服务。"""
from fractions import Fraction
import math
from pathlib import Path

from device_evidence import bound, require, ref

POLICY = 'idle_first_shared'
MEMORY_OVERRIDES = {'device_memory_utilization', 'kv_cache_memory_bytes', 'kv_cache_memory',
                    'num_gpu_blocks_override', 'cpu_offload_gb', 'kv_offloading_size', 'swap_space'}


class CapacityBlocked(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.diagnostic = {'code': code, 'message': message, **details}


def validate(spec, devices):
    require(isinstance(spec, dict), '容量配方须为对象')
    for key in ('weight_bytes', 'kv_bytes_per_token', 'context_tokens', 'runtime_bytes_per_device'):
        require(type(spec.get(key)) is int and spec[key] > 0, '容量配方缺少有效 ' + key)
    fraction = spec.get('memory_fraction')
    require(type(fraction) in (int, float) and math.isfinite(fraction) and 0 < fraction < 1, '显存预算比例无效')
    parallel = spec.get('allowed_tp')
    require(isinstance(parallel, list) and bool(parallel)
            and all(type(n) is int and n > 0 for n in parallel), 'allowed_tp 无效')
    replicated = spec.get('kv_replicated_bytes_per_token', 0)
    require(type(replicated) is int and replicated >= 0, 'KV 复制预算无效')
    device_records(devices)


def device_records(devices):
    require(isinstance(devices, list), '设备观察须为列表')
    for device in devices:
        require(isinstance(device, dict), '设备容量观察须为对象')
        require(all(type(device.get(k)) is int and device[k] >= 0 for k in ('id', 'free_bytes', 'total_bytes')),
                '设备容量观察无效')
        require(type(device.get('occupied')) is bool and 0 < device['total_bytes']
                and device['free_bytes'] <= device['total_bytes'], '设备占用/总容量无效')
    require(len({d['id'] for d in devices}) == len(devices), '设备观察重复')


def effective_fraction(spec, selected, reserves):
    upper = min(Fraction(str(spec['memory_fraction'])),
                *(Fraction(d['free_bytes'] - r, d['total_bytes']) for d, r in zip(selected, reserves)))
    scale = 10 ** 9
    fraction = (upper.numerator * scale // upper.denominator) / scale
    # argv 将以 float 的同一十进制表示传给框架；保护乘法舍入的边界。
    while any(d['total_bytes'] * fraction > d['free_bytes'] - r for d, r in zip(selected, reserves)):
        fraction = math.nextafter(fraction, 0)
    return fraction


def budget(spec, selected):
    count = len(selected)
    shared = any(d['occupied'] for d in selected)
    reserves = [(d['free_bytes'] + 19) // 20 if shared else 0 for d in selected]
    fraction = effective_fraction(spec, selected, reserves)
    weight = (spec['weight_bytes'] + count - 1) // count
    kv = max((spec['kv_bytes_per_token'] + count - 1) // count, spec.get('kv_replicated_bytes_per_token', 0))
    overhead = spec['runtime_bytes_per_device']
    required = weight + overhead + kv * spec['context_tokens']
    rows = []
    for device, reserve in zip(selected, reserves):
        planned = int(device['total_bytes'] * fraction)
        rows.append({**device, 'reserve_bytes': reserve, 'budget_bytes': planned,
                     'required_bytes': required, 'shortfall_bytes': max(0, required - planned)})
    capacity = min(max(0, (row['budget_bytes'] - weight - overhead) // kv) for row in rows)
    return {'policy': POLICY, 'devices': [d['id'] for d in selected], 'tensor_parallel_size': count,
            'sharing_mode': 'shared' if shared else 'exclusive', 'budgets': rows,
            'memory_fraction': fraction, 'kv_token_capacity': capacity, 'context_tokens': spec['context_tokens'],
            'selection_reason': '共享组合满足容量，每卡预留5%剩余显存' if shared else '全空闲组合满足容量'}


def observation(reference):
    report = bound(reference)
    require(isinstance(report, dict), '容量观察须为对象')
    require(report.get('status') == 'completed' and report.get('operation') == 'observe', '分配缺少 completed 观察')
    value = report['result']
    require(isinstance(value, dict), '容量观察结果须为对象')
    device_records(value['devices'])
    source = value['npu_evidence']
    require(ref(source['path']) == source, '容量原始证据摘要变化')
    require(Path(source['path']).read_text(encoding='utf-8') == value['npu_raw'], '容量原文与观察不一致')
    host = value['host_identity']
    require(isinstance(host, dict) and bool(host.get('hostname')) and bool(host.get('boot_id')), '容量缺少主机身份')
    return value


def receipt(reference):
    report = bound(reference)
    require(isinstance(report, dict), '分配回执须为对象')
    require(report.get('status') == 'completed' and report.get('operation') == 'allocate', '缺少 completed 分配回执')
    inputs, result = report['inputs'], report['result']
    require(isinstance(inputs, dict) and isinstance(result, dict), '分配输入及结果须为对象')
    require(inputs.get('policy') == POLICY, '分配回执没有共享策略')
    validate(inputs['spec'], inputs['devices'])
    observed = observation(inputs['observation'])
    by_id = {row['id']: row for row in observed['devices']}
    require(all(by_id.get(row['id']) == row for row in inputs['devices']), '分配输入未绑定设备观察')
    pool = {row['id']: row for row in inputs['devices']}
    ids = result['devices']
    require(isinstance(ids, list) and bool(ids) and all(type(n) is int and n in pool for n in ids)
            and len(set(ids)) == len(ids), '分配超出允许设备池')
    requested = inputs['requested']
    require((type(requested) is int and requested == -1) or requested == ids, '分配改变显式设备选择')
    require(len(ids) in inputs['spec']['allowed_tp'], '分配 TP 不受配方支持')
    expected = budget(inputs['spec'], [pool[n] for n in ids])
    require(result == expected and result['kv_token_capacity'] >= result['context_tokens'], '分配预算或结果不一致')
    return inputs, result, observed


def command_options(command):
    result = {}
    for index, token in enumerate(command):
        if not token.startswith('--'):
            continue
        flag, equal, value = token.partition('=')
        require(flag not in result, '预算声明存在重复启动选项')
        result[flag] = value if equal else next(iter(command[index + 1:index + 2]), '')
    return result


def check_command(request, result):
    options = command_options(request['service']['command'])
    require(not {'--' + name.replace('_', '-') for name in MEMORY_OVERRIDES} & options.keys(), '启动参数绕过已核验显存预算')
    require(float(options['--gpu-memory-utilization']) == result['memory_fraction'], '启动显存比例与分配不一致')
    require(int(options['--tensor-parallel-size']) == result['tensor_parallel_size'], '启动 TP 与分配不一致')
    require(int(options['--max-model-len']) == result['context_tokens'], '启动上下文与分配不一致')


def load(request):
    reference = request.get('npu', {}).get('allocation')
    if reference is None:
        return None
    inputs, result, observed = receipt(reference)
    require(request['npu']['devices'] == result['devices'], '选卡与分配回执不一致')
    check_command(request, result)
    return inputs, result, observed


def configuration(request):
    loaded = load(request)
    if loaded is None:
        return request.get('npu', {})
    result = loaded[1]
    return {'devices': result['devices'], 'policy': result['policy'], 'sharing_mode': result['sharing_mode']}
