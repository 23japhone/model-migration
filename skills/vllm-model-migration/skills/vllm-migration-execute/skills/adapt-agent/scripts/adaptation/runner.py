"""采集与核验的终态聚合；失败保留独立分支，不拥有外部进程。"""
from datetime import datetime, timezone
from pathlib import Path
import uuid
import signal

from . import context, contract, review, source, weights
from .evidence import bound, canonical, write_json

ERRORS = (OSError, ValueError, KeyError, TypeError, OverflowError)


def now():
    return datetime.now(timezone.utc).isoformat()


def loaded_context(request):
    receipt = bound(request['context'])
    if (receipt.get('schema_version') != '1.0' or receipt.get('task_id') != 'adapt-agent'
            or receipt.get('status') != 'completed' or receipt.get('operation') != 'capture'
            or receipt.get('conclusions', {}).get('context') != 'pass'):
        raise ValueError('capture 回执身份或终态无效')
    value = contract.validate(receipt['inputs'], 'capture')
    contract.roots(value, 'capture')
    return receipt, value


def checks(request, receipt, attempt):
    original = receipt['inputs']
    result = {'sources': {}, 'errors': []}
    frozen = receipt['details']
    refreshed = context.collect(original)
    result['context'] = refreshed
    result['errors'].extend(refreshed['errors'])
    if refreshed != frozen:result['errors'].append('冻结上下文发生变化')
    for name, item in original['sources'].items():
        try:
            checked = source.inspect(name, item, frozen['sources'][name]['baseline'], attempt)
            result['sources'][name] = checked
            result['errors'].extend(name + '：' + error for error in checked['errors'])
        except ERRORS as error:result['errors'].append(name + '：' + str(error))
    fingerprints = {name: item['fingerprint'] for name, item in result['sources'].items()}
    config_sha = original['model']['files'][original['model']['config']]
    try:result['weights'] = weights.inspect(original, request['weight_mapping'], fingerprints, config_sha)
    except ERRORS as error:result['errors'].append('weights：' + str(error))
    result['targeted'] = review.inspect(request['checks'], original['required_checks'], fingerprints, config_sha)
    result['errors'].extend(result['targeted']['errors'])
    if not any(item['changes'] for item in result['sources'].values()):result['errors'].append('没有实现适配差异，应使用原预检路线')
    # 读取权重和测试资料后再次冻结实际交付源码，禁止过期测试配新源码。
    for name, item in result['sources'].items():
        try:
            if source.inventory(original['sources'][name]['working'])['fingerprint'] != item['fingerprint']:
                result['errors'].append(name + '：核验期间工作源码发生变化')
        except ERRORS as error:result['errors'].append(name + '：最终源码复核失败：' + str(error))
    result['source_fingerprints'] = fingerprints
    return result


def publish(path, manifest):
    try:write_json(path, manifest)
    except KeyboardInterrupt as error:
        manifest['status'] = 'failed'
        manifest['error'] = str(error)
        manifest['conclusions']['context'] = 'fail'
        if manifest['operation'] == 'check':manifest['conclusions']['adaptation'] = 'fail'
        previous = {s: signal.signal(s, signal.SIG_IGN) for s in [signal.SIGINT, signal.SIGTERM]}
        try:write_json(path, manifest)
        finally:
            for sig, handler in previous.items():signal.signal(sig, handler)


def run(request, work_root, run_id, operation):
    capture_receipt, original = (None, request) if operation == 'capture' else loaded_context(request)
    target = contract.output(work_root, run_id, contract.roots(original, 'capture'))
    attempt = target / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    path = attempt / 'adaptation-manifest.json'
    manifest = {'schema_version': '1.0', 'skill_version': '0.2.0-dev.1', 'task_id': 'adapt-agent',
        'run_id': run_id, 'operation': operation, 'status': 'running', 'inputs': request,
        'input_identity': canonical(request), 'started_at': now(),
        'conclusions': {'context': 'not_evaluated', 'adaptation': 'not_evaluated',
                        'registration': 'not_evaluated', 'weight_loading': 'not_evaluated',
                        'functional_validation': 'not_evaluated'},
        'evidence': {'manifest': str(path)},
        'uncovered': ['静态核验不证明注册、数学正确、真实权重加载、服务功能、精度或性能；后续使用公开注册/验证入口']}
    try:
        write_json(attempt / 'request.json', request)
        write_json(path, manifest)
        details = context.collect(request) if operation == 'capture' else checks(request, capture_receipt, attempt)
        manifest['details'] = details
        passed = not details['errors']
        manifest['status'] = 'completed' if passed else 'blocked'
        manifest['conclusions']['context'] = 'pass' if passed else 'fail'
        if operation == 'check':manifest['conclusions']['adaptation'] = 'pass' if passed else 'fail'
    except (KeyboardInterrupt, *ERRORS) as error:
        manifest['status'] = 'failed'
        manifest['error'] = str(error)
    manifest['finished_at'] = now()
    publish(path, manifest)
    code = 0 if manifest['status'] == 'completed' else (3 if manifest['status'] == 'blocked' else 1)
    return code, {'manifest': str(path), 'status': manifest['status'], 'conclusions': manifest['conclusions']}
