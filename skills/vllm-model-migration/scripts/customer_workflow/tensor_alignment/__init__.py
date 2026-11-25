"""张量比较的客户容差与冻结输入身份。"""
from sys import float_info


def validate_policy(policy):
    if not isinstance(policy, dict) or (policy and set(policy) != {'atol', 'rtol'}):
        raise ValueError('tensor_alignment 只接受空对象或同时提供 atol、rtol')
    for key, value in policy.items():
        if type(value) not in (float, int) or not 0 <= value <= float_info.max:
            raise ValueError('tensor_alignment.' + key + ' 须为有限非负数')


def tolerance_change(previous, current):
    """已经开启三遍时，仅允许改变其离线容差。"""
    first, second = previous.get('accuracy', {}), current.get('accuracy', {})
    if not all(isinstance(a.get('tensor_alignment'), dict) for a in (first, second)):
        return False
    return previous == {**current, 'accuracy': {**second, 'tensor_alignment': first['tensor_alignment']}}


def same_source(previous, source, current):
    old = previous.get('task_source', {})
    return ((old == source and previous['inputs'] == current)
            or (isinstance(old, dict) and isinstance(source, dict) and old.get('path') == source.get('path')
                and previous['inputs'] != current
                and tolerance_change(previous['inputs'], current)))
