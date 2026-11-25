"""Token 对齐的客户输入和定向诊断交接。"""
from sys import float_info


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {'raw_logits', 'processed_scores'}:
        raise ValueError('token_alignment 需要 raw_logits.atol 和 processed_scores.atol')
    for name, entry in policy.items():
        if not isinstance(entry, dict) or set(entry) != {'atol'}:
            raise ValueError(name + ' 只接受 atol')
        value = entry['atol']
        if type(value) not in (int, float) or not 0 <= value <= float_info.max:
            raise ValueError(name + '.atol 须为有限非负数')
