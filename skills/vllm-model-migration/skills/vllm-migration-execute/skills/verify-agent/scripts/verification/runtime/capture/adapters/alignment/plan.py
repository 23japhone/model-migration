"""校验一次定点采集的输入、调用和槽位选择，以及实际 worker 前向。"""
import hashlib
import json
from pathlib import Path
import re


def read_plan(path, digest, hardware='npu'):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('定向采集计划摘要已变化')
    plan = json.loads(raw)
    validate(plan, hardware)
    return raw, plan


def tokens(value):
    return isinstance(value, list) and bool(value) and all(type(i) is int and i >= 0 for i in value)


def validate(plan, hardware='npu'):
    if not isinstance(plan, dict) or plan.get('format') != 'tensor-plan-v1':
        raise ValueError('无效的定向采集计划')
    validate_context(plan)
    unique_ids(plan['calls'])
    keys = set()
    for call in plan['calls']:
        key = validate_call(call, hardware)
        if key in keys:
            raise ValueError('同一运行时调用在计划中重复')
        keys.add(key)


def validate_context(plan):
    prompt, prefix, position = plan['prompt_token_ids'], plan['prefix_token_ids'], plan['position']
    if not tokens(prompt) or not tokens(prefix) or type(position) is not int or position < 1:
        raise ValueError('目标位置、prompt 或共享前文无效')
    if prefix[:len(prompt)] != prompt or len(prefix) != len(prompt) + position - 1:
        raise ValueError('目标位置与共享前文长度不一致')
    if not re.fullmatch('[0-9a-f]{64}', plan['model_identity']):
        raise ValueError('缺少模型协议身份')
    if not plan['case']['subset'] or type(plan['case']['sample_id']) not in (str, int):
        raise ValueError('缺少样本身份')
    phase = 'prefill' if position == 1 else 'decode'
    if type(plan.get('rank', 0)) is not int or plan.get('rank', 0) != 0 or plan.get('phase', phase) != phase:
        raise ValueError('定向计划的执行阶段或 rank 不符')


def unique_ids(items):
    if not isinstance(items, list) or not items:
        raise ValueError('调用或槽位列表为空')
    ids = [item['id'] for item in items]
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('调用或槽位标识为空或重复')


def validate_call(call, hardware='npu'):
    owner, name = call[hardware]['owner'], call[hardware]['name']
    occurrence = call['occurrence']
    if not isinstance(owner, str) or not isinstance(name, str) or not name:
        raise ValueError('运行时调用选择器缺少模块或名称')
    if call['kind'] not in ('module', 'api') or type(occurrence) is not int or occurrence < 0:
        raise ValueError('运行时调用类型或序号无效')
    unique_ids(call['slots'])
    paths = set()
    for slot in call['slots']:
        path = slot[hardware]
        validate_slot(path, slot['role'], call['kind'])
        capture_slice(slot, hardware)
        if tuple(path) in paths:
            raise ValueError('同一调用重复选择同一张量槽位')
        paths.add(tuple(path))
    return call['kind'], owner, name, occurrence


def validate_slot(path, role, kind):
    if not isinstance(path, list) or len(path) < 2 or role not in ('output', 'input', 'weight', 'state'):
        raise ValueError('张量槽位名称或角色无效')
    if any(not (type(k) is int and k >= 0 or isinstance(k, str) and k) for k in path):
        raise ValueError('张量槽位包含无效下标')
    if path[0] not in ('input_args', 'input_kwargs', 'parameters', 'output', 'post_input_args', 'post_input_kwargs'):
        raise ValueError('未知的张量槽位来源')
    if (path[0] in ('output', 'post_input_args', 'post_input_kwargs')) != (role == 'output'):
        raise ValueError('张量槽位方向与角色不匹配')
    if path[0] == 'parameters' and (kind != 'module' or role != 'weight' or len(path) != 2):
        raise ValueError('parameters 仅支持模块的显式参数')


def capture_slice(slot, hardware):
    value = slot.get('capture_slice')
    if value is None:
        return None
    if isinstance(value, dict) and set(value) == {'npu', 'gpu'}:
        value = value[hardware]
    if not isinstance(value, dict) or set(value) != {'axis', 'start', 'stop'}:
        raise ValueError('capture_slice 需要明确维度和左右端点')
    if any(type(n) is not int or n < 0 for n in value.values()) or value['stop'] <= value['start']:
        raise ValueError('capture_slice 索引或半开区间无效')
    return value


def is_target(plan, frame, prompt, observed_outputs):
    if list(prompt) != plan['prompt_token_ids']:
        raise ValueError('实际 prompt 与定向计划不一致')
    if frame['output_index'] != plan['position'] - 1:
        return False
    prefix = [*prompt, *observed_outputs]
    if prefix != plan['prefix_token_ids']:
        raise ValueError('实际生成前缀与定向计划不同')
    positions = frame['positions']
    if not positions or positions[-1] != len(prefix) - 1:
        return False  # 更早的分块 prefill 也可能标记为 output_index=0。
    if positions != list(range(positions[0], len(prefix))) or prefix[positions[0]:] != frame['input_ids']:
        raise ValueError('目标前向的输入行或位置不匹配')
    if frame['logits_indices'] != [len(positions) - 1]:
        raise ValueError('目标前向的 logits 选择行不匹配')
    return True
