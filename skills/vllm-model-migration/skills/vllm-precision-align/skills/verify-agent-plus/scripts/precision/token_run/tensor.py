"""把三遍清单与原生题目、评分协议和两端的首异常绑定。"""
from .. import contract
from ..evidence import bound


def check_prediction(plan, source):
    row = bound(source)
    case, position = plan['case'], plan['position']
    prompt, output = row['prompt_token_ids'], row['output_token_ids']
    if (row['sample_id'] != case['sample_id'] or row.get('subset', case['subset']) != case['subset']
            or type(position) is not int
            or not 1 <= position <= len(output) or prompt != plan['prompt_token_ids']
            or plan['phase'] != ('prefill' if position == 1 else 'decode')
            or prompt + output[:position - 1] != plan['prefix_token_ids']):
        raise ValueError('三遍重放的题目、前文或目标前向与实际预测不同')


def check_plan(source, request):
    contract.reference(source)
    plan = bound(source)
    protocol = request['native_request']['protocol']
    if (plan.get('format') != 'tensor-plan-v1' or plan['quality_report'] != request['quality_report']
            or plan['protocol'] != protocol or plan['model_identity'] != protocol['model_identity']
            or not plan['calls']):
        raise ValueError('三遍采集清单与原始质量测量协议、模型或共同范围不符')
    case = plan['case']
    report = bound(plan['comparison'])
    if (report.get('task_id'), report.get('operation'), report.get('result'), report.get('validity'),
            report.get('first_anomalous_token'), report.get('subset'), report.get('sample_id')) != (
            'vllm-precision-compare', 'token', 'mismatch', 'valid', plan['position'], case['subset'], case['sample_id']):
        raise ValueError('三遍采集清单未绑定有效的二遍首异常')
    if not plan['triggers'] or set(plan['triggers']) != set(report['differences']):
        raise ValueError('三遍触发项与二遍不同')
    for side in ('gpu', 'npu'):
        prediction = plan['predictions'][side]
        if prediction != report['evidence'][side]:
            raise ValueError('三遍使用了不同的预测来源')
        check_prediction(plan, prediction)
    return (case['subset'], case['sample_id']), source


def bind_plans(request, rows):
    plans = {}
    for source in request['tensor_plans']:
        key, source = check_plan(source, request)
        if key in plans:
            raise ValueError('同一题目的三遍清单重复')
        plans[key] = source
    if plans.keys() != {(row['subset'], row['sample_id']) for row in rows}:
        raise ValueError('三遍清单题目集合与定向重跑名单不一致')
    return [{**row, 'tensor_plan': plans[(row['subset'], row['sample_id'])]} for row in rows]


def sealed(seal, source):
    reference = seal['tensor_bundle']
    bundle, plan = bound(reference), bound(source)
    if (bundle.get('format'), bundle.get('hardware'), bundle.get('evidence_kind'), bundle.get('plan_sha256')) != (
            'tensor-bundle-v1', 'npu', 'runtime', source['sha256']):
        raise ValueError('封存回执与三遍 NPU 采集清单不同')
    if any(bundle.get(k) != plan[k] for k in ('case', 'position', 'phase', 'model_identity', 'prefix_token_ids')):
        raise ValueError('封存的 NPU 张量与目标前向不同')
    return reference
