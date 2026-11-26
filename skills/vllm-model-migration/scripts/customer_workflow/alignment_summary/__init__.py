"""客户总回执的三层对齐摘要，只读投影已绑定的公开报告。"""
from . import tensor, token
from .evidence import ERRORS, fields
from .presentation import Layer


def token_scopes(manifest, layer, policy):
    quality = manifest['task_quality_conclusion']
    if not quality['reports']:
        layer.gap({}, '尚无可用于定界的任务质量报告')
    seen = set()
    for source in quality['reports']:
        origin = fields(source, ('dataset', 'report'))
        if source['precision_target'] == 'pass':
            continue
        if source['precision_target'] != 'fail':
            reason = '、'.join(source.get('missing', [])) or '任务质量判据或可比证据不足'
            layer.gap(origin, reason + '，无法确定诊断范围')
            continue
        identity = (source['report']['path'], source['report']['sha256'])
        if identity in seen:
            continue
        seen.add(identity)
        for score in source.get('scores', []):
            if 'target' in score and score['precision_target'] == 'not_evaluated':
                layer.gap({**origin, **fields(score, ('metric', 'subset'))}, '该约定目标尚无法判定，待定界')
        try:
            token.scope_cases(manifest, source, layer, policy)
        except ERRORS as exc:
            layer.gap(origin, str(exc))


def tensor_scopes(manifest, predictions, layer, policy):
    for gap in predictions.gaps:
        layer.gap(gap, 'Token 定界不足：' + str(gap['reason']))
    by_report = {}
    for row in predictions.cases:
        origin = fields(row, ('dataset', 'subset', 'sample_id', 'quality_report'))
        if row['result'] in ('input_mismatch', 'insufficient_evidence'):
            layer.gap(origin, '缺少同一前文下的可比预测：' + str(row['reason']))
        elif row['evidence_kind'] == 'unspecified':
            layer.gap(origin, 'Token 预测的双端运行或模拟来源未核实')
        if row['result'] == 'mismatch':
            by_report.setdefault(row['token_report']['sha256'], []).append(row)
    for origin in predictions.reports:
        scope = fields(origin, ('dataset', 'quality_report', 'token_report'))
        eligible = by_report.get(scope['token_report']['sha256'], [])
        if eligible:
            try:
                tensor.scope_cases(manifest, scope, eligible, layer, policy)
            except ERRORS as exc:
                for row in eligible:
                    layer.cases.append({**scope, **fields(row, ('subset', 'sample_id')),
                                        'position': row['first_anomalous_token'],
                                        'result': 'insufficient_evidence', 'reason': str(exc)})


def inactive(manifest, requested, intent_known):
    if manifest['status'] == 'not_run':
        return 'not_run'
    if manifest['status'] == 'running':
        return 'pending'
    if not requested:
        return 'not_requested' if intent_known else 'insufficient_evidence'
    return None


def hardware_status(layer):
    statuses = {row.get('hardware_target_status') for row in layer['cases'] if row.get('evidence_kind') == 'runtime'}
    if 'fail' in statuses:
        return 'fail'
    return 'pass' if layer['result'] == 'aligned' and statuses == {'pass'} else 'not_evaluated'


def tensor_target(layer, policy):
    status = hardware_status(layer)
    layer['hardware_target_status'] = status
    if layer['cases']:
        labels = {'pass': '已达标', 'fail': '未达标', 'not_evaluated': '尚无法判定'}
        layer['summary'] += '硬件目标' + labels[status] + '。'
        if policy:
            layer['summary'] += '指定容差 ' + '、'.join(f'{k}={v}' for k, v in policy.items()) + '。'
    reference_labels = {'aligned': '与共同 CPU 参考一致', 'mismatch': '与共同 CPU 参考有差异',
                        'not_evaluated': '共同 CPU 参考不足'}
    for outcome, label in reference_labels.items():
        count = sum(row['cpu_check'].get('common_reference', 'not_evaluated') == outcome
                    for row in layer['cases'] if row.get('comparison'))
        if count:
            layer['summary'] += f'{count} 题{label}。'


def summarize(manifest, accuracy, *, intent_known):
    predictions = Layer('Token 预测', '任务质量没有已确认的超限项')
    tensors = Layer('算子与张量', '没有可定位到同一前文的双端分数或选词异常')
    tensor_requested = 'tensor_alignment' in accuracy or bool(manifest.get('tensor_alignment'))
    token_requested = 'token_alignment' in accuracy or bool(manifest.get('token_alignment')) or tensor_requested
    token_state = inactive(manifest, token_requested, intent_known)
    tensor_state = inactive(manifest, tensor_requested, intent_known)
    if token_state is None:
        token_scopes(manifest, predictions, accuracy.get('token_alignment'))
    if tensor_state is None:
        tensor_scopes(manifest, predictions, tensors, accuracy.get('tensor_alignment', {}))
    for layer, state in ((predictions, token_state), (tensors, tensor_state)):
        if state == 'insufficient_evidence':
            layer.gap({}, '尚未确认本次诊断意图')
    token_result = predictions.finish(token_state)
    tensor_result = tensors.finish(tensor_state)
    tensor_target(tensor_result, accuracy.get('tensor_alignment'))
    quality = manifest['task_quality_conclusion']
    headline = quality['summary'].partition('。')[0] + '。'
    return {'summary': headline + token_result['summary'] + tensor_result['summary'],
            'task_quality': quality, 'token_prediction': token_result, 'operator_tensor': tensor_result}
