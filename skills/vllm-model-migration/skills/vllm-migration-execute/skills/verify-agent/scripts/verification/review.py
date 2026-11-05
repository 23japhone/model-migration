"""不可变生成证据的逐项语义复核，以及精确owned交接停止。"""
from pathlib import Path
import json

from .evidence import begin, finish, read_bound, sha256, write_json
from .runtime import carrier
from . import lifecycle, outputs


def review_source(source):
    expected = {'schema_version': '1.0', 'task_id': 'verify-agent', 'operation': 'verify', 'status': 'completed'}
    if not isinstance(source, dict) or any(source.get(k) != v for k, v in expected.items()):
        raise ValueError('原验证报告的共同版本、任务或终态不一致')
    if not isinstance(source.get('run_id'), str) or not source['run_id'].strip():
        raise ValueError('原验证报告缺少运行身份')
    if any(not isinstance(source.get(key), dict) for key in ('inputs', 'conclusions', 'evidence', 'detailed_report')):
        raise ValueError('原验证报告缺少共同外层或详细结果')


def verify_review(source, judgments):
    review_source(source)
    if source['conclusions'].get('semantic') != 'review_required':
        raise ValueError('原验证报告不是语义待复核状态')
    if any(v != 'pass' for k, v in source['conclusions'].items() if k != 'semantic'):
        raise ValueError('其他必需检查未通过，不能用语义判断掩盖')
    results = required_cases(source)
    complete_evidence(source)
    pending = {c['id']: c for c in results if c['semantic'] == 'review_required'}
    if set(judgments) != set(pending):raise ValueError('必须完整复核且只能复核本次待复核的必需用例')
    for name, case in pending.items():validate_judgment(case, judgments[name])
    return all(v['verdict'] == 'pass' for v in judgments.values())


def required_cases(source):
    required = {'inputs', 'runtime', 'registration', 'workers', 'functional_protocol', 'semantic', 'lifecycle'}
    if required - source['conclusions'].keys():raise ValueError('原报告缺少必需验收结论')
    actual = case_coverage(source)
    if any(v['protocol'] != 'pass' or v['semantic'] not in ('pass', 'review_required') for v in actual):
        raise ValueError('必需用例存在未通过协议或语义，不能用复核掩盖')
    for case in actual:response_evidence(case)
    return actual


def case_coverage(source):
    planned = source['inputs'].get('cases')
    results = source['detailed_report'].get('cases')
    if not isinstance(planned, list) or not isinstance(results, list):raise ValueError('原请求或用例结果缺失')
    expected = {v['id']: v['kind'] for v in planned if v.get('required') is True}
    actual = [v for v in results if v.get('required') is True]
    if not expected or len(actual) != len(expected) or {v['id']: v['kind'] for v in actual} != expected:
        raise ValueError('必需用例覆盖与原始请求不一致')
    return actual


def complete_evidence(source):
    evidence = source['evidence']
    for name in ['inputs', 'cases', 'handoff']:
        if name not in evidence or not Path(evidence[name]).is_file():raise ValueError('原验证证据缺失')
    read_bound({'path': evidence['handoff'], 'sha256': source['detailed_report']['handoff_sha256']})
    if json.loads(Path(evidence['cases']).read_text()) != source['detailed_report']['cases']:
        raise ValueError('原始用例清单与不可变报告不一致')


def response_evidence(case):
    response = case['response']
    if sha256(response['path']) != response['sha256']:raise ValueError('原响应内容发生变化')
    if sha256(response['raw_path']) != response['raw_sha256']:raise ValueError('原始HTTP/SSE证据发生变化')


def validate_judgment(case, judgment):
    if (set(judgment) != {'verdict', 'reason', 'response_sha256'}
            or judgment['verdict'] not in ('pass', 'fail') or not str(judgment['reason']).strip()
            or judgment['response_sha256'] != case['response']['sha256']):
        raise ValueError('语义判断缺少有效结论、理由或响应身份')
    response_evidence(case)


def review(request, target, run_id):
    source = read_bound(request['source'])
    outputs.check(target, outputs.protected_roots(source['inputs']))
    accepted = verify_review(source, request['cases'])
    attempt, manifest = begin(target, request, run_id, 'review')
    manifest['conclusions'] = {**source['conclusions'], 'semantic': 'pass' if accepted else 'fail'}
    manifest['uncovered'] = source['uncovered']
    manifest['evidence'] = {**source['evidence'], 'source': request['source']['path']}
    manifest['detailed_report'] = {'review': request['cases'], 'source_sha256': request['source']['sha256'],
                                   'service_state': '沿用原报告交接快照；本操作未启停服务'}
    return lifecycle.complete(manifest, 'completed')


def stop(request, target, run_id):
    handoff = read_bound(request['handoff'])
    outputs.check(target, outputs.protected_roots(handoff))
    resource = handoff['resource']
    if resource['ownership'] != 'owned':raise ValueError('borrowed 交接不得停止')
    attempt, manifest = begin(target, request, run_id, 'stop')
    status = lifecycle.stop(resource, attempt, manifest)
    manifest['uncovered'] = ['本次只停止已核对的owned资源，没有运行模型功能或精度验收']
    return lifecycle.complete(manifest, status)
