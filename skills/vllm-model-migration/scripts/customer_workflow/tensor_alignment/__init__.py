"""张量比较的客户容差与冻结输入身份。"""
from sys import float_info


def validate_policy(policy):
    if not isinstance(policy, dict) or policy.keys() - {'atol', 'rtol'}:
        raise ValueError('tensor_alignment 仅接受 atol、rtol')
    for key, value in policy.items():
        if type(value) not in (float, int) or not 0 <= value <= float_info.max:
            raise ValueError('tensor_alignment.' + key + ' 须为有限非负数')


def tolerance_change(previous, current):
    """同一任务可补填、移除或调整张量容差。"""
    def other_inputs(task):
        accuracy = dict(task.get('accuracy', {}))
        accuracy.pop('tensor_alignment', None)
        return {**task, 'accuracy': accuracy}

    return other_inputs(previous) == other_inputs(current)


def same_source(previous, source, current):
    old = previous.get('task_source', {})
    return ((old == source and previous['inputs'] == current)
            or (isinstance(old, dict) and isinstance(source, dict) and old.get('path') == source.get('path')
                and previous['inputs'] != current
                and tolerance_change(previous['inputs'], current)))
