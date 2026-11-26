"""通过比较任务的公开 CLI 判断每道题的首个异常并核验返回的证据。"""
import sys

from alignment.evidence import EvidenceError, read, ref, safe, write
from alignment.process import call
from alignment.request import release_skill
from .bundles import DATA_ERRORS, reproduce
from .selection import case_key

RESULTS = {'aligned', 'mismatch', 'early_stop', 'stop_reason_mismatch', 'input_mismatch', 'insufficient_evidence'}


def compare_case(directory, entries, tolerances, cpu_inputs):
    request = {'case': {k: entries['gpu'][k] for k in ('subset', 'sample_id')},
               'token_alignment': tolerances, 'gpu': entries['gpu']['record'], 'npu': entries['npu']['record'],
               **cpu_inputs}
    path = directory / 'request.json'
    output = directory / 'comparison.json'
    write(path, request)
    cli = release_skill('vllm-precision-compare') / 'scripts/compare.py'
    args = [sys.executable, '-B', str(cli), '--action', 'token', '--request', str(path), '--output', str(output)]
    # CPU 复算独享四小时的子进程预算，外层还须读取双端预测并交付最终回执。
    code, message = call(args, 15000, directory, 'compare')
    if code:
        raise ValueError('公开 Token 比较失败：' + safe(message[:400]))
    report = read(output)
    if (report.get('task_id'), report.get('operation'), report.get('status')) != (
            'vllm-precision-compare', 'token', 'completed'):
        raise ValueError('Token 比较回执缺少有效任务身份')
    if report.get('result') not in RESULTS or report['evidence'].get('request') != ref(path):
        raise ValueError('Token 比较结果或请求引用无效')
    if any(report['evidence'].get(side) != request[side] for side in ('gpu', 'npu')):
        raise ValueError('Token 比较回执未绑定当前 GPU/NPU 记录')
    return report, ref(output)


def verify_case_identity(report, key, kinds):
    if report.get('sample_id') != key[1] or report.get('subset') not in (None, key[0]):
        raise ValueError('单题比较回执中的题目身份不符')
    for side, kind in kinds.items():
        declared = report.get('evidence_kind', {}).get(side)
        if declared in ('synthetic', 'runtime') and declared != kind:
            raise ValueError('预测包与记录的实际/模拟来源不符：' + side)


def process_case(case, index, indexed, directory, request, cpu_inputs):
    key = case_key(case)
    entries = {device: indexed.get((device, key)) for device in ('gpu', 'npu')}
    row = {**case, 'result': 'insufficient_evidence', 'first_anomalous_token': None, 'validity': 'not_evaluated',
           'evidence_kind': {side: e['evidence_kind'] for side, e in entries.items() if e}}
    missing = [side for side, entry in entries.items() if not entry]
    if missing:
        row['reason'] = '缺少唯一预测记录：' + ', '.join(missing)
        return row
    row['quality_reproduction'] = reproduce(case, entries, request['quality_report']['sha256'])
    child = directory / f'case-{index}'
    child.mkdir()
    try:
        report, source = compare_case(child, entries, request['token_alignment'], cpu_inputs)
        verify_case_identity(report, key, row['evidence_kind'])
        row.update({k: report[k] for k in ('result', 'reason', 'validity', 'first_anomalous_token', 'first_missing_token',
                                           'stop_after_tokens', 'covered_tokens', 'cpu_check') if k in report})
        row['comparison'] = source
    except EvidenceError:
        raise
    except (*DATA_ERRORS, RuntimeError) as error:
        row['reason'] = safe(error)
    return row


def aggregate(rows, selection):
    if not rows or any(row['result'] == 'insufficient_evidence' for row in rows):
        return 'insufficient_evidence'
    if selection['pending_scopes']:
        return 'partially_evaluated'
    if any('synthetic' in row['evidence_kind'].values() for row in rows):
        return 'simulated'
    if any(row['result'] == 'input_mismatch' for row in rows):
        return 'input_mismatch'
    return 'aligned' if all(row['result'] == 'aligned' for row in rows) else 'mismatch'
