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


def keyed(rows, field):
    index = {row[field]: row for row in rows}
    require(len(index) == len(rows), '张量计划或报告存在重复调用或槽位')
    return index


def call_coverage(call, measured_slots):
    planned = keyed(call['slots'], 'id')
    observed = keyed(measured_slots, 'slot')
    require(observed.keys() <= planned.keys(), '报告含计划以外的槽位')
    results = {name: observed.get(name, {}).get('result') for name in planned}
    return {'call': call['id'], 'planned': len(planned),
            'assessed': sum(value in ('pass', 'fail') for value in results.values()),
            'failed': [slot for slot, value in results.items() if value == 'fail'],
            'unassessed': [slot for slot, value in results.items() if value not in ('fail', 'pass')]}


def checked_measurements(report, plan):
    result = report['result']
    require(result in {'aligned', 'mismatch', 'insufficient_evidence', 'not_evaluated'}, '未知双端张量结果')
    cpu = report.get('cpu_check', {})
    require(isinstance(cpu, dict), 'CPU 参考结果格式无效')
    planned, observed = keyed(plan['calls'], 'id'), keyed(report['calls'], 'id')
    require(observed.keys() <= planned.keys(), '报告含计划以外的调用')
    calls = [call_coverage(call, observed.get(name, {}).get('slots', [])) for name, call in planned.items()]
    if result == 'aligned':
        missing = [f"{c['call']}.{slot}" for c in calls for slot in c['unassessed']]
        require(not missing, '计划槽位缺少判定：' + '、'.join(missing))
        require(calls and all(c['planned'] and c['planned'] == c['assessed'] and not c['failed'] for c in calls),
                '缺少全部计划槽位的一致证据')
    if result == 'mismatch':
        require(any(c['failed'] for c in calls), '差异报告缺少超限调用与槽位')
    first = report.get('first_exceedance')
    if first is not None:
        require(any(c['call'] == first['call'] and first['slot'] in c['failed'] for c in calls), '首个超限槽位不符')
    return calls


def measured(row, upstream, policy, plan):
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
    hardware = report.get('hardware_target_status', 'not_evaluated') if kind == 'runtime' else 'not_evaluated'
    require(hardware in ('pass', 'fail', 'not_evaluated'), '硬件目标状态无效')
    if hardware == 'pass':
        require((report['result'], report['cpu_check']['common_reference']) == ('aligned', 'aligned'),
                '硬件通过结论缺少双端一致和共同 CPU 参考')
    detail = {**fields(report, ('result', 'first_exceedance', 'capture_status', 'coverage', 'scope_uncovered',
                              'target_status')), 'calls': checked_measurements(report, plan),
            'cpu_check': report.get('cpu_check', {'common_reference': 'not_evaluated'}),
            'hardware_target_status': hardware,
            'comparison': row['comparison'], 'evidence_kind': kind, 'tolerance': policy}
    if report['result'] == 'not_evaluated' and not policy:
        detail['reason'] = '已有双端比较，本次未指定张量容差'
    return detail


def project_case(row, upstream, policy):
    require(row and row.get('source_result') == 'mismatch', '缺少与 Token 异常对应的第三遍记录')
    detail = fields(row, ('plan', 'reproduction', 'scope_uncovered', 'gpu_capture_status', 'npu_capture_status'))
    if 'position' in row:
        require(row['position'] == upstream['first_anomalous_token'], '张量记录的异常位置不符')
    plan = checked_plan(row, upstream) if row.get('plan') else None
    if row.get('comparison'):
        require(plan, '张量比较缺少绑定计划')
        detail.update(measured(row, upstream, policy, plan))
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
    active = {key for key, row in rows.items() if row and row.get('result') != 'not_triggered'}
    if active.difference(case_key(row) for row in eligible):
        layer.gap(scope, '张量报告含已确认 Token 首异常范围以外的比较')
    for upstream in eligible:
        item = {**scope, **fields(upstream, ('subset', 'sample_id')), 'position': upstream['first_anomalous_token']}
        try:
            item.update(project_case(rows.get(case_key(upstream)), upstream, policy))
        except ERRORS as exc:
            item.update(result='insufficient_evidence', reason=str(exc))
        layer.cases.append(item)
