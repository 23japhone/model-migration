"""冻结比较范围、张量身份和数值判定条件。"""
from decimal import Decimal
import math

from ..evidence import path_of


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' 须为非空字符串')
    return value


def natural(value):
    return type(value) is int and value >= 0


def numbers(values):
    return isinstance(values, list) and all(natural(value) for value in values)


def policy(value):
    if value == {}:
        return None
    if not isinstance(value, dict) or set(value) != {'atol', 'rtol'}:
        raise ValueError('tensor_alignment 须同时提供 atol、rtol 或使用空对象')
    for tolerance in value.values():
        if type(tolerance) not in (int, float, Decimal) or not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError('数值容差须为有限非负数')
    return {name: float(tolerance) for name, tolerance in value.items()}


def request(value):
    sides = set(value) & {'gpu', 'npu'}
    if not sides or set(value) != sides | {'plan', 'tensor_alignment'}:
        raise ValueError('张量比较须提供 plan、tensor_alignment 及至少一侧采集包')
    policy(value['tensor_alignment'])
    for field in sides | {'plan'}:
        ref = value[field]
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
            raise ValueError(field + ' 缺少完整文件引用')
        path_of(ref['path'])


def check_case(plan):
    case = plan['case']
    if not isinstance(case, dict) or set(case) != {'subset', 'sample_id'}:
        raise ValueError('张量计划缺少具体子集和题目')
    text(case['subset'], 'subset')
    if type(case['sample_id']) is not int:
        text(case['sample_id'], 'sample_id')
    prefix, prompt, pos = plan['prefix_token_ids'], plan['prompt_token_ids'], plan['position']
    if not numbers(prefix) or not numbers(prompt) or not prompt or not natural(pos) or pos < 1:
        raise ValueError('张量计划前文或输出位置无效')
    if prefix[:len(prompt)] != prompt or len(prefix) != len(prompt) + pos - 1:
        raise ValueError('张量计划前文与异常输出位置不符')


def argument(path, role, kind):
    if not isinstance(path, list) or len(path) < 2:
        raise ValueError('须指定实际参数位置')
    if any(not ((isinstance(k, str) and k) or natural(k)) for k in path):
        raise ValueError('参数索引无效')
    sources = ('input_args', 'input_kwargs', 'parameters', 'output', 'post_input_args', 'post_input_kwargs')
    if path[0] not in sources or (path[0] in sources[3:]) != (role == 'output'):
        raise ValueError('参数位置与槽位职责不同')
    if path[0] == 'parameters' and (kind != 'module' or role != 'weight' or len(path) != 2):
        raise ValueError('模块权重须指定实际参数名')


def capture_region(slot, side):
    region = slot.get('capture_slice')
    if region is None:
        return None
    if isinstance(region, dict) and set(region) == {'gpu', 'npu'}:
        region = region[side]
    validate_transform({'slice': region})
    if region['stop'] <= region['start']:
        raise ValueError('采集时的有效区间为空')
    return region


def check_slot(slot, kind):
    text(slot['id'], 'slot.id')
    if slot['role'] not in ('input', 'weight', 'output', 'state'):
        raise ValueError('未知张量职责')
    if type(slot.get('allow_matching_inf', False)) is not bool:
        raise ValueError('allow_matching_inf 须为布尔值')
    for side in ('gpu', 'npu'):
        argument(slot[side], slot['role'], kind)
        capture_region(slot, side)
    transforms = slot.get('transforms', {})
    if not isinstance(transforms, dict) or transforms.keys() - {'gpu', 'npu'}:
        raise ValueError('无效的张量布局转换')
    for steps in transforms.values():
        if not isinstance(steps, list):
            raise ValueError('张量布局转换须为有序列表')
        for step in steps:
            validate_transform(step)


def validate_transform(step):
    if not isinstance(step, dict) or len(step) != 1:
        raise ValueError('张量转换须为 permute 或 slice')
    if 'permute' in step and numbers(step['permute']) and sorted(step['permute']) == list(range(len(step['permute']))):
        return
    if 'slice' in step:
        part = step['slice']
        if isinstance(part, dict) and set(part) == {'axis', 'start', 'stop'} and all(natural(x) for x in part.values()):
            if part['start'] <= part['stop']:
                return
    raise ValueError('无效的轴排列或半开切片')


def check_call(call):
    text(call['id'], 'call.id')
    if call['kind'] not in ('module', 'api') or not natural(call['occurrence']):
        raise ValueError('调用类型或序号无效')
    for side in ('gpu', 'npu'):
        target = call[side]
        text(target['name'], side + '.name')
        if not isinstance(target['owner'], str):
            raise ValueError('调用归属字段须为模块路径')
    slots = call['slots']
    if not isinstance(slots, list) or not slots:
        raise ValueError('每次调用需声明预期张量')
    for slot in slots:
        check_slot(slot, call['kind'])
    if len({s['id'] for s in slots}) != len(slots):
        raise ValueError('调用内 slot 重复')


def validate_plan(plan):
    if plan.get('format') != 'tensor-plan-v1':
        raise ValueError('张量计划格式无效')
    check_case(plan)
    if not plan.get('model_identity'):
        raise ValueError('缺少模型身份')
    if plan.get('rank', 0) != 0 or plan.get('phase', phase(plan)) != phase(plan):
        raise ValueError('计划采集阶段或单卡 rank 无效')
    calls = plan['calls']
    if not isinstance(calls, list) or not calls:
        raise ValueError('张量计划没有比较范围')
    for call in calls:
        check_call(call)
    if len({c['id'] for c in calls}) != len(calls):
        raise ValueError('张量计划中存在重复调用')
    for side in ('gpu', 'npu'):
        identities = [(c['kind'], c[side]['owner'], c[side]['name'], c['occurrence']) for c in calls]
        if len(set(identities)) != len(calls):
            raise ValueError(side + ' 一次实际调用被多个计划项重复匹配')


def phase(plan):
    return 'prefill' if plan['position'] == 1 else 'decode'
