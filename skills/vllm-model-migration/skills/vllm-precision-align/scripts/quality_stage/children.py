"""只通过两个任务的公开 CLI 执行测量、历史核验及质量比较。"""
from pathlib import Path
import sys

from alignment.evidence import bound, parse, read, ref, safe, write
from alignment.process import call
from alignment.request import reference, release_skill


class PublicSourceError(ValueError):
    """公开 GPU 分数的固定来源失效。"""


def completed_location(action, code, stdout, label):
    try:
        returned = parse(stdout)
    except ValueError as error:
        raise ValueError(f'{label} 未交付原生回执，退出码 {code}，见 {label}.stderr.log') from error
    if code or returned.get('status') != 'completed':
        raise ValueError(safe(returned.get('diagnostic') or returned.get('summary') or f'{label} 退出 {code}'))
    return Path(returned['report_path'])


class Children:
    def __init__(self, attempt, run_id, timeout):
        self.attempt = attempt
        self.run_id = run_id
        self.timeout = timeout

    def native(self, action, request, label):
        path = self.attempt / (label + '.request.json')
        write(path, request)
        root = self.attempt / 'children' / label
        cli = release_skill('verify-agent-plus') / 'scripts/validator.py'
        argv = [sys.executable, '-B', str(cli), '--action', action, '--request', str(path),
                '--work-root', str(root), '--run-id', self.run_id]
        code, stdout = call(argv, self.timeout, self.attempt, label)
        receipt_path = completed_location(action, code, stdout, label)
        if not receipt_path.is_relative_to(root) or receipt_path.resolve() != receipt_path:
            raise ValueError('原生回执不属于本次委托')
        receipt = ref(receipt_path)
        value = bound(receipt)
        if (value.get('run_id') != self.run_id or value.get('task_id') != 'verify-agent-plus'
                or value.get('status') != 'completed' or value.get('conclusions', {}).get('evaluation_validity') != 'pass'):
            raise ValueError('原生回执缺少有效测量或运行身份不符')
        if value.get('inputs') != request:
            raise ValueError('原生回执输入与本次委托不符')
        projection = value['evidence']['quality_evidence']
        reference(projection)
        bound(projection)
        return receipt, projection

    def inspect_gpu(self, source, label='inspect-gpu'):
        cli = release_skill('vllm-precision-compare') / 'scripts/compare.py'
        code, stdout = call([sys.executable, '-B', str(cli), '--inspect-gpu', source],
                            self.timeout, self.attempt, label)
        if code:
            detail = (self.attempt/(label+'.stderr.log')).read_text(encoding='utf-8').strip()
            raise ValueError(detail[:800] or f'GPU 原生归档复核退出 {code}')
        return parse(stdout)

    def compare(self, baseline, measured, gpu, basis='customer_provided'):
        path = self.attempt / 'compare.request.json'
        request = {key: measured[key]['path'] for key in ('measurement', 'receipt', 'verification')}
        request.update(baseline=baseline, source=measured['source'], baseline_source=basis)
        if gpu:
            request['gpu_evidence'] = gpu['path']
        write(path, request)
        report = self.attempt / 'quality-report.json'
        cli = release_skill('vllm-precision-compare') / 'scripts/compare.py'
        args = [sys.executable, '-B', str(cli), '--request', str(path), '--output', str(report)]
        code, _ = call(args, self.timeout, self.attempt, 'compare')
        if code:
            raise ValueError('质量比较未交付报告，详见 compare.stderr.log')
        evidence = read(report)['evidence']
        for key in ('receipt', 'verification', 'measurement'):
            if evidence.get(key) != measured[key]:
                raise ValueError('比较报告的证据与经过阶段核验的测量不同：' + key)
        if gpu and evidence.get('gpu_evidence') not in (None, gpu):
            raise ValueError('GPU 发布文件在核验与出报告之间发生变化')
        return ref(report)
