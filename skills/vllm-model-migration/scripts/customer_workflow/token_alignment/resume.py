"""新交付第三遍材料时，沿用同一二遍比较及其原始题号和文件身份。"""
from coordination.runtime import sources


def prior(session, key, request):
    receipt = (session.previous or {}).get('steps', {}).get(key, {}).get('receipt')
    if not receipt:
        return None, []
    stage = sources.bound(receipt)
    if (stage.get('task_id'), stage.get('operation'), stage.get('status'), stage.get('run_id')) != (
            'vllm-precision-align', 'token', 'completed', session.value['run_id']):
        return None, []
    inputs = stage['inputs']
    if any(inputs.get(k) != request.get(k) for k in ('quality_report', 'token_alignment', 'native_request')):
        return None, []
    report = sources.bound(stage['token_report'])
    if report['quality_report'] != inputs['quality_report'] or report['selection'] != stage['selection']:
        return None, []
    sources.bound(stage['selection'])
    saved = list(inputs['bundles'])
    measured = report.get('npu_measurement', {}).get('bundle')
    if measured:
        saved.append(measured)
    if reusable(request['bundles'], saved, report['cases']):
        for row in report['cases']:
            detail = sources.bound(row['comparison'])
            for side in ('request', 'gpu', 'npu'):
                sources.bound(detail['evidence'][side])
        return stage, []
    return None, saved


def reusable(current, saved, cases):
    return all(item in saved for item in current) and all(
        row.get('comparison') and row['result'] != 'insufficient_evidence' for row in cases)


def merge(current, saved):
    """保留其他设备/题目的整包。重复单题交由二遍现有歧义检查。"""
    return [*current, *(source for source in saved if source not in current)]
