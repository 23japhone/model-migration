"""把选题清单和单题 Token 比较关联到首轮质量报告。"""
from coordination.runtime import sources
from .evidence import (ERRORS, case_key, comparison, evidence_kind, fields, indexed, one_entry,
                       records, require, stage_report)

DIFFERENCES = {'mismatch', 'early_stop', 'stop_reason_mismatch'}
LABELS = {'raw_logits': '原始 logits 超差', 'processed_scores': '处理后概率超差', 'selected_token': '选词不同'}


def describe(report):
    result = report['result']
    if result == 'mismatch':
        position = report['first_anomalous_token']
        require(type(position) is int and position > 0, '缺少首个异常输出位置')
        kinds = '、'.join(LABELS[key] for key in report['differences'])
        require(kinds, '缺少异常类型')
        return f'第 {position} 个输出位置{kinds}'
    if result == 'stop_reason_mismatch':
        return f"输出 {report['stop_after_tokens']} 个 Token 后停止原因不同"
    if result == 'early_stop':
        return f"第 {report['first_anomalous_token']} 个输出位置一端已提前停止"
    return report.get('reason') or '缺少单题预测依据'


def checked_case(row, policy):
    if row['result'] == 'insufficient_evidence' and not row.get('comparison'):
        return {'result': 'insufficient_evidence', 'reason': row.get('reason') or '缺少双端预测记录'}
    report = comparison(row, 'token')
    subset, sample = case_key(row)
    require(report.get('subset') in (None, subset) and type(report['sample_id']) is type(sample)
            and report['sample_id'] == sample, 'Token 比较题号不符')
    require(row.get('first_anomalous_token') == report.get('first_anomalous_token'), '首异常位置与比较报告不符')
    require(report['result'] in DIFFERENCES | {'aligned', 'input_mismatch', 'insufficient_evidence'}, '未知预测结果')
    if report['result'] in DIFFERENCES | {'aligned'}:
        require(report.get('validity') == 'valid', '单题比较有效性未确认')
    request = sources.bound(report['evidence']['request'])
    require(case_key(request['case']) == case_key(row), '单题比较请求题号不符')
    require(policy is None or request['token_alignment'] == policy, '预测比较容差与本次约定不符')
    for side in ('gpu', 'npu'):
        require(request[side] == report['evidence'][side], '预测记录与比较请求不符')
        kind = report['evidence_kind'].get(side, 'unspecified')
        require(kind in ('unspecified', row['evidence_kind'].get(side)), '预测来源类型不一致')
    return {**fields(report, ('result', 'first_anomalous_token', 'first_missing_token', 'stop_after_tokens', 'stop_reasons',
                              'covered_tokens', 'differences', 'gpu_token_id', 'npu_token_id', 'cpu_check')),
            'reason': describe(report), 'evidence_kind': evidence_kind(row['evidence_kind']),
            'tolerance': request['token_alignment'], 'comparison': row['comparison']}


def scope_cases(manifest, source, layer, policy):
    scope = {'dataset': source['dataset'], 'quality_report': source['report']}
    entry = one_entry(manifest, 'token_alignment', source['report'])
    report = stage_report(manifest, entry, 'token')
    scope['token_report'] = entry['token_report']
    selection = sources.bound(report['selection'])
    require((selection.get('format'), selection.get('quality_report'), selection.get('dataset')) ==
            ('token-case-selection-v1', source['report'], source['dataset']), '选题清单不属于本份质量报告')
    if entry.get('selection'):
        require(entry['selection'] == report['selection'], '选题清单引用不符')
    layer.reports.append({**scope, 'selection': report['selection'], 'scope_coverage': selection.get('scope_coverage', []),
                          'uncovered': report.get('uncovered', [])})
    selected = indexed(selection['cases'], layer, scope)
    compared = indexed(report['cases'], layer, scope)
    for pending in records(selection['pending_scopes']):
        if isinstance(pending, dict):
            layer.gap(scope, pending.get('reason') or '未定界的分项缺少原因', **fields(pending, ('metric', 'subset')))
        else:
            layer.gap(scope, '选题清单含无法识别的待定分项')
    if not selected:
        layer.gap(scope, '失败分项尚未定界到具体题号')
    if compared.keys() - selected.keys():
        layer.gap(scope, '报告含选题清单以外的题号')
    for (subset, sample), choice in selected.items():
        item = {**scope, 'subset': subset, 'sample_id': sample}
        try:
            row = compared.get((subset, sample))
            require(row and choice, '缺少唯一的已选题比较记录')
            require(row['triggers'] == choice['triggers'], '题目绑定的失败指标不一致')
            item.update(triggers=choice['triggers'], quality_reproduction=row.get('quality_reproduction', []))
            item.update(checked_case(row, policy))
        except ERRORS as exc:
            item.update(result='insufficient_evidence', reason=str(exc))
        layer.cases.append(item)
