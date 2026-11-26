"""读取首轮有效 NPU 测量的模型协议与失败子集题号名册。"""
from alignment.evidence import bound, safe
from alignment.request import reference
from .selection import case_key

SOURCE_ERRORS = (OSError, ValueError, TypeError, KeyError)


def stable_roster(refs, source, checked):
    details = checked['detailed_report']
    if source == refs['receipt']:
        return details.get('stable_samples', {})
    if checked.get('operation') != 'quality-analyze' or checked['inputs']['source'] != refs['receipt']:
        raise ValueError('历史 NPU 复核未绑定原始回执')
    if 'stable_samples' in details:
        return details['stable_samples']
    original = bound(refs['receipt'])
    if (original.get('task_id'), original.get('status'), original['conclusions'].get('evaluation_validity'),
            original['detailed_report'].get('hardware')) != ('verify-agent-plus', 'completed', 'pass', 'npu'):
        raise ValueError('首轮 NPU 名册缺少已验收的原始测量')
    return original['detailed_report'].get('stable_samples', {})


def read_npu(refs, dataset):
    measurement = refs['measurement']
    reference(measurement)
    source = refs.get('verification') or refs['receipt']
    reference(source)
    receipt = bound(source)
    if (receipt.get('task_id') != 'verify-agent-plus' or receipt.get('status') != 'completed'
            or receipt.get('conclusions', {}).get('evaluation_validity') != 'pass'
            or receipt.get('detailed_report', {}).get('hardware') != 'npu'
            or receipt.get('evidence', {}).get('quality_evidence') != measurement):
        raise ValueError('首轮 NPU 成绩与原生有效回执不符')
    projected = bound(measurement)
    if (projected.get('hardware'), projected.get('format'), projected['protocol']['dataset']) != (
            'npu', 'task-quality-native-v1', dataset):
        raise ValueError('首轮 NPU 逐题来源设备、格式或数据集不符')
    return projected, stable_roster(refs, source, receipt)


class NativeScores:
    def __init__(self, report, needed):
        self.npu = {}
        self.stable = {}
        self.issues = []
        if not needed:
            return
        refs = report.get('evidence') or {}
        dataset = report['baseline']['dataset']
        if 'measurement' in refs:
            try:
                self.npu, self.stable = read_npu(refs, dataset)
            except SOURCE_ERRORS as error:
                self.issues.append('NPU 原生逐题补取失败：' + safe(error))

    def roster(self, subset):
        identifiers = self.stable.get(subset, [])
        if not isinstance(identifiers, list):
            raise ValueError('NPU 稳定题号名册格式无效')
        for identifier in identifiers:
            case_key({'subset': subset, 'sample_id': identifier})
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('NPU 子集题号名册重复')
        return identifiers
