"""冻结有 GPU 对照数据的题目，通过评测任务公开入口进行定向 NPU 复测。"""
from pathlib import Path
import sys

from alignment.evidence import EvidenceError, bound, parse, ref, safe, write
from alignment.process import call
from alignment.request import reference, release_skill
from .bundles import DATA_ERRORS, package
from .selection import case_key


def run_measurement(request, destination, run_id):
    root = destination / 'measurements'
    path = destination / 'token-run.request.json'
    write(path, request)
    cli = release_skill('verify-agent-plus') / 'scripts/validator.py'
    argv = [sys.executable, '-B', str(cli), '--action', 'token-run', '--request', str(path),
            '--work-root', str(root), '--run-id', run_id]
    code, stdout = call(argv, 14400, destination, 'token-run')
    if code not in (0, 1):
        raise ValueError('NPU 定向测量未交付：' + safe(stdout[-400:]))
    summary = parse(stdout)
    file = Path(summary['report_path'])
    if not file.is_relative_to(root) or file.resolve() != file:
        raise ValueError('NPU 原生回执不属于当前测量目录')
    receipt = ref(file)
    manifest = bound(receipt)
    if (manifest.get('task_id'), manifest.get('operation'), manifest.get('run_id')) != (
            'verify-agent-plus', 'token-run', run_id):
        raise ValueError('NPU 原生回执运行身份无效')
    if manifest.get('inputs') != request:
        raise ValueError('NPU 回执与本次委托请求不一致')
    source = manifest['evidence']['token_bundle']
    reference(source)
    info = package(source, request['quality_report']['sha256'])
    if info['hardware'] != 'npu' or info['evidence_kind'] != 'runtime':
        raise ValueError('NPU 测量未返回真实本端预测包')
    expected = bound(request['selection'])['cases']
    errors = manifest.get('detailed_report', {}).get('case_errors', [])
    check_coverage(expected, info['cases'], errors)
    status = terminal_status(code, manifest, errors)
    result = {'status': status, 'receipt': receipt, 'bundle': source}
    if errors:
        result['case_errors'] = errors
    return result


def terminal_status(code, manifest, errors):
    status = (code, manifest.get('status'), manifest.get('success'), bool(errors))
    if status == (0, 'completed', True, False):
        return 'completed'
    if status == (1, 'failed', False, True):
        return 'partial'
    raise ValueError('NPU 子进程状态、退出码与记录的逐题覆盖不一致')


def check_coverage(selected, captured, failed):
    if not isinstance(failed, list) or not captured:
        raise ValueError('NPU 无有效预测或失败清单格式无效')
    expected = {case_key(item) for item in selected}
    seen = [case_key(item) for item in [*captured, *failed]]
    if any(not isinstance(row.get('message'), str) or not row['message'] for row in failed):
        raise ValueError('部分测量未提供逐题失败原因')
    if len(seen) != len(set(seen)) or set(seen) != expected:
        raise ValueError('NPU 成功与失败记录未逐项覆盖且仅覆盖本次选题')


def collect_npu(request, selection, selection_ref, indexed, output, run_id, first_protocol):
    if 'native_request' not in request:
        return None
    selected = [case for case in selection['cases']
                if indexed.get(('gpu', case_key(case))) and not indexed.get(('npu', case_key(case)))]
    if not selected:
        return None
    if not first_protocol or first_protocol != request['native_request'].get('protocol'):
        return {'status': 'unavailable', 'selection': selection_ref, 'reason': '第二遍原生协议与首轮已核验测量未绑定或不一致'}
    if len(selected) != len(selection['cases']):
        path = output / 'npu-selection.json'
        write(path, {**selection, 'cases': selected})
        selection_ref = ref(path)
    child = {'schema_version': '1.0', 'native_request': request['native_request'],
             'selection': selection_ref, 'quality_report': request['quality_report']}
    try:
        return run_measurement(child, output, run_id)
    except EvidenceError:
        raise
    except (*DATA_ERRORS, RuntimeError) as error:
        return {'status': 'unavailable', 'selection': selection_ref, 'reason': safe(error)}
