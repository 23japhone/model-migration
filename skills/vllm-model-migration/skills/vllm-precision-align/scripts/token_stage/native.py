"""只读补充质量报告未声明的逐题成绩或经首轮验收的子集题号名册。"""
import subprocess
import sys

from alignment.evidence import bound, digest, parse, safe
from alignment.request import reference, release_skill
from .selection import case_key

SOURCE_ERRORS = (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError)


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
    return projected, receipt['detailed_report'].get('stable_samples', {})


def read_gpu(source, dataset):
    reference(source)
    if digest(source['path']) != source['sha256']:
        raise ValueError('首轮 GPU 来源摘要不符')
    cli = release_skill('vllm-precision-compare') / 'scripts/compare.py'
    args = [sys.executable, '-B', str(cli), '--inspect-gpu', source['path'], '--include-samples']
    result = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise ValueError('无法核验首轮 GPU 原生逐题成绩：' + safe(result.stderr[:300]))
    projected = parse(result.stdout)
    if digest(source['path']) != source['sha256'] or projected['protocol']['dataset'] != dataset:
        raise ValueError('首轮 GPU 原生来源身份不符')
    return projected


def sample_index(data, scope):
    candidates = [row for row in data.get('scores', []) if
                  (row.get('subset'), row.get('metric')) == (scope['subset'], scope['metric'])]
    if len(candidates) != 1 or candidates[0].get('error') or candidates[0].get('direction') != scope['direction']:
        return {}
    indexed = {}
    for item in candidates[0].get('samples', []):
        key = case_key(item)
        if key in indexed or scope['subset'] not in (None, key[0]):
            raise ValueError('原生逐题身份重复或归属不符')
        indexed[key] = item['score']
    return indexed


class NativeScores:
    def __init__(self, report, needed):
        self.npu = {}
        self.gpu = {}
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
        if self.npu and 'gpu_evidence' in refs:
            try:
                self.gpu = read_gpu(refs['gpu_evidence'], dataset)
            except SOURCE_ERRORS as error:
                self.issues.append('GPU 原生逐题补取失败：' + safe(error))

    def pairs(self, row):
        gpu = sample_index(self.gpu, row)
        npu = sample_index(self.npu, row)
        return {key: (gpu[key], value) for key, value in npu.items() if key in gpu}

    def roster(self, subset):
        identifiers = self.stable.get(subset, [])
        if not isinstance(identifiers, list):
            raise ValueError('NPU 稳定题号名册格式无效')
        for identifier in identifiers:
            case_key({'subset': subset, 'sample_id': identifier})
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('NPU 子集题号名册重复')
        return identifiers
