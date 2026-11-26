"""沿已核验的 Token 首异常和单题计划投影张量报告。"""
from coordination.runtime import sources
from .evidence import (ERRORS, case_key, comparison, evidence_kind, fields, indexed, one_entry,
                       require, stage_report)


def checked_plan(row, upstream):
    plan = sources.bound(row['plan'])
    require((plan.get('format'), case_key(plan['case']), plan['position']) ==
            ('tensor-plan-v1', case_key(upstream), upstream['first_anomalous_token']), '采集计划题号或位置不符')
    require((plan['quality_report'], plan['comparison']) ==
            (upstream['quality_report'], upstream['comparison']), '采集计划绑定了其他质量报告或预测比较')
    return plan


def slot_coverage(report):
    result = []
    for call in report['calls']:
        slots = call['slots']
        result.append({'call': call['id'], 'planned': len(slots),
                       'assessed': sum(slot['result'] in ('pass', 'fail') for slot in slots),
                       'failed': [slot['slot'] for slot in slots if slot['result'] == 'fail'],
                       'unassessed': [slot['slot'] for slot in slots if slot['result'] not in ('fail', 'pass')]})
    return result


def measured(row, upstream, policy):
    report = comparison(row, 'tensor')
    require((case_key(report['case']), report['position'], report['plan'], report['tensor_alignment']) ==
            (case_key(upstream), upstream['first_anomalous_token'], row['plan'], policy), '张量题号、位置、计划或容差不符')
    require(row['reproduction']['status'] == 'reproduced', '缺少目标步复现证据')
    for side in ('gpu', 'npu'):
        require(report['evidence'][side] == row[side], '张量包引用不符')
    require(report['evidence']['plan'] == row['plan'], '张量比较来源计划不符')
    kind = evidence_kind(report['evidence_kind'])
    if upstream['evidence_kind'] == 'synthetic':
        kind = 'synthetic'
    elif upstream['evidence_kind'] != 'runtime' and kind == 'runtime':
        kind = 'unspecified'
    return {**fields(report, ('result', 'first_exceedance', 'capture_status', 'coverage', 'scope_uncovered',
                              'cpu_check', 'target_status')), 'calls': slot_coverage(report),
            'comparison': row['comparison'], 'evidence_kind': kind, 'tolerance': policy}


def project_case(row, upstream, policy):
    require(row and row.get('source_result') == 'mismatch', '缺少与 Token 异常对应的第三遍记录')
    detail = fields(row, ('plan', 'reproduction', 'scope_uncovered'))
    if row.get('plan'):
        checked_plan(row, upstream)
    if row.get('comparison'):
        require(row.get('plan'), '张量比较缺少绑定计划')
        detail.update(measured(row, upstream, policy))
    else:
        detail.update(result='insufficient_evidence', stage_result=row['result'],
                      reason=row.get('reason') or row.get('npu_measurement_issue') or '缺少有效的双端张量比较')
    return detail


def scope_cases(manifest, scope, eligible, layer, policy):
    entry = one_entry(manifest, 'tensor_alignment', scope['quality_report'])
    require(entry.get('token_report') == scope['token_report'], '第三遍记录属于另一份 Token 报告')
    report = stage_report(manifest, entry, 'tensor')
    require(report['token_report'] == scope['token_report'], '第三遍报告的 Token 来源不符')
    scope = {**scope, 'tensor_report': entry['tensor_report']}
    layer.reports.append({**scope, 'uncovered': report.get('uncovered', [])})
    rows = indexed(report['cases'], layer, scope)
    for upstream in eligible:
        item = {**scope, **fields(upstream, ('subset', 'sample_id')), 'position': upstream['first_anomalous_token']}
        try:
            item.update(project_case(rows.get(case_key(upstream)), upstream, policy))
        except ERRORS as exc:
            item.update(result='insufficient_evidence', reason=str(exc))
        layer.cases.append(item)
