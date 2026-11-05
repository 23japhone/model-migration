"""复用有效原生评测：经公开历史核验后仍交付原 run 回执。"""
from .delegation import common, delegate, exit_code, precision_details
from .evidence import bound


def evaluation(item, child, attempt, run_id, timeout):
    source = item['result']
    original = bound(source)
    common(original, 'verify-agent-plus')
    if original.get('operation') != 'run' or original.get('status') != 'completed':
        raise ValueError('只能复用已完成的原始精度运行')
    if original.get('inputs') != child:
        raise ValueError('精度续接输入或部署身份变化，不能复用旧测量')
    exit_code(original, 'evaluations')
    precision_details(original)
    checked, check_ref = delegate('evaluations', item['id'] + '-reuse-check', {'source': source},
                                  attempt, run_id, timeout, 'analyze')
    if checked.get('status') != 'completed' or checked['conclusions'].get('evaluation_validity') != 'pass':
        raise ValueError('旧原生产物身份或有效性复核未通过')
    return original, source, check_ref
