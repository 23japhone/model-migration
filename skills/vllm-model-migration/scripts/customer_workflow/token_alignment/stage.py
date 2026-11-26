"""把一次不可变的质量比较与其二遍定向预测证据关联。"""
from coordination.runtime import sources
from ..invoke import execute
from .resume import merge, prior


def bundles(context, quality_report):
    result = []
    for ref in context.get('evidence', []):
        try:
            data = sources.bound(ref)
        except ValueError:
            # context.evidence 也接受原生文档或文本资料，仍要求文件身份保持不变。
            if sources.ref(ref['path']) != ref:
                raise
            continue
        if (data.get('format') == 'token-prediction-bundle-v1'
                and data.get('quality_report_sha256') == quality_report['sha256']):
            result.append(ref)
    return result


def compare_one(session, row, policy, context, work_root, key):
    report = row['report']
    quality = sources.bound(report)
    if (quality.get('conclusions', {}).get('evaluation_validity') != 'pass'
            or not any(item.get('comparability') == 'pass' and item.get('precision_target') == 'fail'
                       for item in quality.get('scores', []))):
        return {'status': 'not_triggered', 'quality_report': report}
    request = {'schema_version': '1.0', 'quality_report': report, 'token_alignment': policy,
               'bundles': bundles(context, report)}
    if row.get('stage'):
        stage = sources.bound(row['stage'])
        if stage.get('quality_report') != report:
            raise ValueError('任务质量阶段交接与当前质量报告不同')
        request['native_request'] = stage['inputs']['native_request']
    outcome, saved = prior(session, key, request)
    if outcome is None:
        request['bundles'] = merge(request['bundles'], saved)
        outcome = execute(session, key, 'vllm-precision-align', request, work_root, action='token')
    return {**{field: outcome[field] for field in ('status', 'alignment_status', 'selection', 'token_report')
               if field in outcome}, 'quality_report': report, 'stage': session.value['steps'][key]['receipt']}


def compare_all(session, policy, context, root, guarded):
    results = []
    for index, row in enumerate(session.value.get('task_quality', [])):
        key = f'token-{index}'
        if row.get('report'):
            item = guarded(session, key, lambda: compare_one(session, row, policy, context, root, key))
        else:
            item = {'status': 'not_triggered', 'reason': '任务质量报告缺失'}
        results.append(item or {'status': 'blocked', 'step': key})
    session.value['token_alignment'] = results
    session.save()
