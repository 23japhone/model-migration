"""客户总回执的三层对齐摘要，只读投影已绑定的公开报告。"""
from . import tensor, token
from .evidence import ERRORS, fields
from .presentation import Layer


def token_scopes(manifest, layer):
    quality = manifest['task_quality_conclusion']
    if not quality['reports']:
        layer.gap({}, '尚无可用于定界的任务质量报告')
    seen = []
    for source in quality['reports']:
        origin = fields(source, ('dataset', 'report'))
        if source['precision_target'] == 'pass':
            continue
        if source['precision_target'] != 'fail':
            layer.gap(origin, '任务质量判据或可比证据不足，无法确定诊断范围')
            continue
        if source['report'] in seen:
            continue
        seen.append(source['report'])
        try:
            token.scope_cases(manifest, source, layer)
        except ERRORS as exc:
            layer.gap(origin, str(exc))


def tensor_scopes(manifest, predictions, layer, policy):
    for gap in predictions.gaps:
        layer.gap(gap, 'Token 定界不足：' + str(gap['reason']))
    for row in predictions.cases:
        if row['result'] in ('input_mismatch', 'insufficient_evidence'):
            layer.gap(fields(row, ('dataset', 'subset', 'sample_id', 'quality_report')),
                      '缺少同一前文下的可比预测：' + str(row['reason']))
    for origin in predictions.reports:
        scope = fields(origin, ('dataset', 'quality_report', 'token_report'))
        eligible = [row for row in predictions.cases
                    if row['token_report'] == scope['token_report'] and row['result'] == 'mismatch']
        if eligible:
            try:
                tensor.scope_cases(manifest, scope, eligible, layer, policy)
            except ERRORS as exc:
                for row in eligible:
                    layer.cases.append({**scope, **fields(row, ('subset', 'sample_id')),
                                        'position': row['first_anomalous_token'],
                                        'result': 'insufficient_evidence', 'reason': str(exc)})


def inactive(manifest, requested, intent_known):
    if manifest['status'] in ('not_run', 'running'):
        return 'not_run'
    if not requested:
        return 'not_requested' if intent_known else 'insufficient_evidence'
    return None


def tensor_target(layer, policy):
    status = {'mismatch': 'fail', 'aligned': 'pass'}.get(layer['result'], 'not_evaluated')
    layer['target_status'] = status
    if layer['cases']:
        labels = {'pass': '已达标', 'fail': '未达标', 'not_evaluated': '尚无法判定'}
        layer['summary'] += '双端张量目标' + labels[status] + '。'
        if policy:
            layer['summary'] += '指定容差 ' + '、'.join(f'{k}={v}' for k, v in policy.items()) + '。'


def summarize(manifest, accuracy, *, intent_known):
    predictions = Layer('Token 预测')
    tensors = Layer('算子与张量')
    tensor_requested = 'tensor_alignment' in accuracy or bool(manifest.get('tensor_alignment'))
    token_requested = 'token_alignment' in accuracy or tensor_requested
    token_state = inactive(manifest, token_requested, intent_known)
    tensor_state = inactive(manifest, tensor_requested, intent_known)
    if token_state is None:
        token_scopes(manifest, predictions)
    if tensor_state is None:
        tensor_scopes(manifest, predictions, tensors, accuracy.get('tensor_alignment', {}))
    for layer, state in ((predictions, token_state), (tensors, tensor_state)):
        if state == 'insufficient_evidence':
            layer.gap({}, '尚未确认本次诊断意图')
    token_result = predictions.finish(token_state)
    tensor_result = tensors.finish(tensor_state, direct=True)
    tensor_target(tensor_result, accuracy.get('tensor_alignment'))
    quality = manifest['task_quality_conclusion']
    return {'summary': quality['summary'] + token_result['summary'] + tensor_result['summary'],
            'task_quality': quality, 'token_prediction': token_result, 'operator_tensor': tensor_result}
