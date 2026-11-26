"""同一质量报告可分次补齐预测包，第三遍所引用的完整 Token 报告保持原身份。"""
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
    saved = list(inputs['bundles'])
    try:
        report = sources.bound(stage['token_report'])
        if report['quality_report'] != inputs['quality_report'] or report['selection'] != stage['selection']:
            return None, saved
        sources.bound(stage['selection'])
        measured = report.get('npu_measurement', {}).get('bundle')
        if measured:
            saved.append(measured)
        accuracy = session.value.get('declared_inputs', session.value['inputs'])['accuracy']
        if 'tensor_alignment' in accuracy and reusable(request['bundles'], saved, report['cases']):
            return stage, []
    except sources.ERRORS:
        # 子报告无法复核时重新比较。冻结请求中的预测包仍由公开入口逐包验摘要。
        pass
    return None, saved


def reusable(current, saved, cases):
    if not all(item in saved for item in current):
        return False
    for row in cases:
        if not row.get('comparison') or row['result'] == 'insufficient_evidence':
            return False
        detail = sources.bound(row['comparison'])
        for side in ('request', 'gpu', 'npu'):
            sources.bound(detail['evidence'][side])
    return True


def merge(current, saved):
    """保留其他设备/题目的整包。重复单题交由二遍现有歧义检查。"""
    return [*current, *(source for source in saved if source not in current)]
