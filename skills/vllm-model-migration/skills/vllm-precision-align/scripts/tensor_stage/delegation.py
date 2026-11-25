"""跨 Skill 的逐题比较与 NPU 定向复测，核对公开进程和持久化回执。"""
from pathlib import Path
import sys

from alignment.evidence import bound, parse, read, ref, safe, write
from alignment.process import call
from alignment.request import release_skill


def compare(folder, label, action, request):
    path = folder / (label + '.request.json')
    result = folder / (label + '.report.json')
    write(path, request)
    cli = release_skill('vllm-precision-compare') / 'scripts/compare.py'
    args = [sys.executable, '-B', str(cli), '--action', action, '--request', str(path), '--output', str(result)]
    code, message = call(args, 14400, folder, label)
    if code:
        raise ValueError('精度比较未交付有效报告：' + safe(message[-400:]))
    report = read(result)
    if (report.get('task_id'), report.get('operation')) != ('vllm-precision-compare', action):
        raise ValueError('比较回执任务身份或终态不符')
    if (action == 'token' and report.get('status') != 'completed'
            or action == 'tensor' and report.get('format') != 'tensor-comparison-v1'):
        raise ValueError('公开比较没有返回对应的持久化报告')
    for key in ('gpu', 'npu', 'plan'):
        if key in request and report['evidence'].get(key) != request[key]:
            raise ValueError('比较回执未绑定当前 ' + key)
    if report['evidence'].get('request') != ref(path):
        raise ValueError('比较回执未绑定当前请求')
    return report, ref(result)


def capture(folder, run_id, native, selection, plan):
    path = folder / 'tensor-run.request.json'
    request = {'schema_version': '1.0', 'native_request': native, 'selection': selection,
               'quality_report': bound(plan)['quality_report'], 'tensor_plans': [plan]}
    write(path, request)
    cli = release_skill('verify-agent-plus') / 'scripts/validator.py'
    work = folder / 'measurements'
    args = [sys.executable, '-B', str(cli), '--action', 'tensor-run', '--request', str(path),
            '--work-root', str(work), '--run-id', run_id]
    code, stdout = call(args, 14400, folder, 'tensor-run')
    summary = parse(stdout)
    report_path = Path(summary['report_path'])
    if not report_path.is_relative_to(work) or report_path.resolve() != report_path:
        raise ValueError('NPU 回执不属于本次独立尝试')
    source = ref(report_path)
    receipt = bound(source)
    if (receipt.get('task_id'), receipt.get('operation'), receipt.get('run_id'), receipt.get('inputs')) != (
            'verify-agent-plus', 'tensor-run', run_id, request):
        raise ValueError('NPU 回执任务或来源不符')
    errors = receipt.get('detailed_report', {}).get('case_errors', [])
    completed = (code, receipt.get('status'), receipt.get('success')) == (0, 'completed', True)
    partial = (code, receipt.get('status'), receipt.get('success')) == (1, 'failed', False)
    if not ((completed and not errors) or (partial and errors)):
        raise ValueError('NPU 第三遍测量未完成：' + safe(errors))
    bundles = receipt['evidence']['tensor_bundles']
    if not isinstance(bundles, list) or len(bundles) != 1:
        raise ValueError('单题 NPU 测量缺少唯一张量包')
    return bundles[0], source
