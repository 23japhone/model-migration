"""续接选择、人工决策与已有部署身份；不重新执行服务或评分。"""
from pathlib import Path
import hashlib

from coordination.runtime import sources, receipts, continuation
from .effects import operation, OperationBlocked


class DecisionRequired(ValueError):
    def __init__(self, message, decision):
        super().__init__(message)
        self.decision = {'owner': 'user', 'message': message, **decision}


def capacity_decision(report):
    diagnostic = report.get('diagnostic', {})
    if diagnostic.get('code') == 'capacity_insufficient':
        raise DecisionRequired('允许卡的剩余显存不足，请更换其他卡或释放显存后继续',
                               {'code': 'capacity_insufficient', 'capacity': diagnostic})


def allocate(session, request, work_root):
    from . import effects
    try:
        return effects.operation(session, 'allocation', 'verify-agent', request, work_root)
    except OperationBlocked as error:
        capacity_decision(error.report)
        raise


def migration_capacity(value):
    reference = value.get('tasks', {}).get('verify', {}).get('receipt')
    if value.get('status') == 'blocked' and reference:
        capacity_decision(sources.bound(reference))


def container(session, deployment, observed, decisions, work_root):
    current = observed.get('container')
    if not current:
        return
    identity = {key: current[key] for key in ('container_id', 'image_id', 'started_at')}
    request = {'operation': 'replace_container', 'container_name': deployment['container_name'], 'identity': identity}
    if current.get('status') != 'exited' or current.get('running') is not False:
        consent = decisions.get('container_replacement', {})
        if consent.get('approved') is not True or consent.get('identity') != identity:
            raise DecisionRequired('同名容器运行中或未证实已退出，请确认是否删除后重建；授权须绑定当前容器身份',
                                   {'container_name': deployment['container_name'], 'identity': identity})
        request['consent'] = consent
    operation(session, 'container_replacement', 'verify-agent', request, work_root)


def migration(session, source):
    value = sources.bound(source)
    receipts.validate(value, Path(source['path']), 'migration', 'vllm-migration-execute')
    continuation.reusable(value)
    if value['status'] != 'completed':
        raise ValueError('Agent 续接需要已完成的迁移阶段回执')
    prior = session.value['steps'].get('migration', {})
    if 'request' not in prior:
        raise ValueError('外部迁移回执缺少本客户任务生成的原始请求')
    original = sources.bound(value['inputs']['source']) if 'source' in value['inputs'] else value
    if original['inputs'] != sources.bound(prior['request']):
        raise ValueError('续接迁移改变了客户任务的原始准备、部署或预检请求')
    session.step('migration', 'completed', receipt=source, request=prior['request'])
    return value


def evaluations(previous):
    reference = previous.get('steps', {}).get('entry', {}).get('receipt') if previous else None
    if not reference:
        return {}
    entry = sources.bound(reference)
    stage = entry.get('stages', {}).get('precision', {}).get('receipt')
    if not stage:
        return {}
    rows = sources.bound(stage)['tasks']['evaluations']
    selected = {}
    for key, row in rows.items():
        if row.get('status') != 'completed' or not row.get('receipt'):
            continue
        value = sources.bound(row['receipt'])
        if value.get('status') == 'completed' and value['conclusions'].get('evaluation_validity') == 'pass':
            selected[key] = row['receipt']
    return selected


def pending(session):
    decisions = [row['decision'] for row in session.value['steps'].values() if row.get('decision')]
    return {'owner': 'user', 'decisions': decisions} if decisions else None


def asset_files(asset, model=False):
    from model_cache import checked
    root = Path(asset['path'])
    for row in asset['files']:
        path = checked(root, row['path']) if model else root / row['path']
        if not model and (path.resolve() != path or not path.is_relative_to(root)):
            raise ValueError('续接数据路径不安全')
        before = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if before != path.stat() or digest.hexdigest() != row['sha256']:
            raise ValueError('已准备资产发生变化，不能复用旧回执：' + row['path'])


def frozen_observation(name, value):
    if name == 'deployment':
        return {key: item for key, item in value.items() if key != 'devices'}
    return value


def freeze(session, name, value):
    value = frozen_observation(name, value)
    frozen = session.value.setdefault('resolved_observations', {})
    if name in frozen and frozen_observation(name, frozen[name]) != value:
        raise ValueError('已经执行的 ' + name + ' 配方不能在续接时改变；请创建新任务')
    frozen[name] = value
    session.save()


def resources(steps):
    row = steps.get('migration', {})
    if row.get('status') == 'running':
        return {'state': 'unknown', 'note': '迁移调用未收敛，读取其自有子任务证据确认资源'}
    if not row.get('receipt'):
        return {'state': 'not_observed', 'note': '未取得迁移回执，不能推断设备已释放'}
    try:
        stage = sources.bound(row['receipt'])
        source = stage.get('tasks', {}).get('verify', {}).get('receipt')
        if not source:
            return {'state': 'not_observed', 'migration': row['receipt']}
        raw = sources.bound(source)
        return {'state': raw.get('detailed_report', {}).get('final_service', {}).get('state', 'unknown'),
                'source': source, 'freshness': '原 verify 观察；当前存活需 runtime_paths 复查',
                'latest_path_observation': steps.get('runtime_paths', {}).get('receipt')}
    except sources.ERRORS:
        return {'state': 'unknown', 'note': '原服务状态来源不可核对，未执行自动清理'}
