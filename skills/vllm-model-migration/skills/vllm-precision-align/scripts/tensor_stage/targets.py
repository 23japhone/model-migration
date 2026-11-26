"""从已完成的两轮报告固定首个可比较异常及实际前文。"""
from alignment.evidence import bound
from alignment.request import reference
from token_stage.native import read_npu
from token_stage.selection import case_key, failing_rows


def upstream(request):
    quality = bound(request['quality_report'])
    failed = failing_rows(quality)
    token = bound(request['token_report'])
    if (token.get('task_id'), token.get('operation'), token.get('quality_report')) != (
            'vllm-precision-align', 'token', request['quality_report']):
        raise ValueError('Token 阶段回执与本次质量报告不符')
    selection = bound(token['selection'])
    dataset = quality['baseline']['dataset']
    if (selection.get('quality_report'), selection.get('dataset'), selection.get('format')) != (
            request['quality_report'], dataset, 'token-case-selection-v1'):
        raise ValueError('Token 选题清单和首轮评测不一致')
    selected = index(selection['cases'])
    scopes = {(score['subset'], score['metric'], score['direction']) for score in failed}
    for (subset, _), item in selected.items():
        validate_triggers(item['triggers'], subset, scopes)
    rows = index(token['cases'])
    if rows.keys() != selected.keys() or (rows and not failed):
        raise ValueError('Token 阶段逐题覆盖与失败范围不一致')
    return quality, token, selected


def validate_triggers(triggers, subset, scopes):
    if not isinstance(triggers, list) or not triggers:
        raise ValueError('冻结的题目缺少退化来源')
    for trigger in triggers:
        scope = trigger['subset']
        if scope not in (None, subset) or (scope, trigger['metric'], trigger['direction']) not in scopes:
            raise ValueError('冻结的题目与首轮未达标分项不同')
        basis = trigger['selection_basis']
        if basis not in ('regressed_sample', 'failed_subset_roster') or (basis == 'failed_subset_roster' and scope is None):
            raise ValueError('冻结的题目缺少逐题退化或失败子集依据')


def index(rows):
    if not isinstance(rows, list):
        raise ValueError('缺少逐题清单')
    values = {case_key(row): row for row in rows}
    if len(values) != len(rows):
        raise ValueError('逐题清单有重复项')
    return values


def comparison_for(case, row):
    source = row['comparison']
    reference(source)
    report = bound(source)
    if (report.get('task_id'), report.get('operation'), report.get('status'), report.get('validity'),
            report.get('result'), case_key(report)) != (
            'vllm-precision-compare', 'token', 'completed', 'valid', 'mismatch', case_key(case)):
        raise ValueError('二遍单题比较证据无效或首异常不一致')
    return report


def make_target(context, case, row):
    quality, request = context
    report = comparison_for(case, row)
    comparison_input = bound(report['evidence']['request'])
    if comparison_input['case'] != case:
        raise ValueError('二遍比较请求未绑定选中题目')
    predictions = {side: report['evidence'][side] for side in ('gpu', 'npu')}
    if any(comparison_input[side] != source for side, source in predictions.items()):
        raise ValueError('二遍预测记录与请求不一致')
    policy = comparison_input['token_alignment']
    position = row['first_anomalous_token']
    if type(position) is not int or position < 1 or position != report['first_anomalous_token']:
        raise ValueError('二遍首异常位置与固定的单题比较回执不同')
    gpu = bound(predictions['gpu'])
    npu = bound(predictions['npu'])
    prompt = gpu['prompt_token_ids']
    prefix = prompt + gpu['output_token_ids'][:position - 1]
    if npu['prompt_token_ids'] != prompt or prefix != prompt + npu['output_token_ids'][:position - 1]:
        raise ValueError('固定的首异常前文不同')
    measurement, _ = read_npu(quality['evidence'], quality['baseline']['dataset'])
    protocol = measurement['protocol']
    identity = protocol['model_identity']
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError('首轮协议缺少模型权重身份')
    vector = gpu['raw_logits'][position - 1]
    vocab_size = len(vector) if isinstance(vector, list) else vector['size']
    return {'format': 'tensor-plan-v1', 'case': case, 'position': position, 'rank': 0,
            'phase': 'prefill' if position == 1 else 'decode', 'prefix_token_ids': prefix,
            'prompt_token_ids': prompt, 'quality_report': request['quality_report'], 'comparison': row['comparison'],
            'predictions': predictions, 'model_identity': identity, 'protocol': protocol, 'token_alignment': policy,
            'capture_mode': {'world_size':1, 'execution':'eager', 'request_count':1,
                             'scheduling':'synchronous', 'input':'text', 'sampling':'greedy'},
            'static_conditions': measurement.get('static_conditions') or {},
            'triggers': sorted(report['differences']), 'vocab_size': vocab_size, 'prediction_evidence_kind': row.get('evidence_kind', {})}
