"""按阶段契约读取输入和采集保护边界，不执行阶段。"""
from pathlib import Path

from ..planning import STAGES
from . import bindings, receipts, sources


class Blocked(ValueError):
    """当前阶段缺少可执行前置条件。"""


def prepare_source(node):
    source = node.get('request', node.get('result'))
    value = sources.bound(source)
    if 'result' in node:
        receipts.validate(value, Path(source['path']), 'preparation', STAGES['preparation'][0])
        if value['status'] != 'completed':
            raise Blocked('已有准备交接未完成')
        value = value['inputs']
    return sources.prepare_request(value, Path(source['path']).parent)


def precision_scope(value, action):
    if value.get('deployments') or any('deployment' in row for row in value.get('evaluations', [])):
        raise Blocked('已有服务精度不允许重新部署；请交接已有服务结果')
    if action == 'analyze' and value.get('datasets'):
        raise Blocked('历史分析不得准备数据')


def preflight(request, work_root, action):
    values, errors, protected = {}, {}, []
    for key, node in request['stages'].items():
        source = node.get('request', node.get('result'))
        protected.append(Path(source['path']))
        if 'result' in node:
            protected.append(Path(source['path']).parent)
        try:
            value = stage_source(key, node)
            values[key] = value
            sources.scan(value, work_root, protected)
            if key in ('preparation', 'dataset_preparation'):
                scan_prepare(value, work_root, protected)
            if 'result' not in node or key != 'migration':
                stage_scope(key, value, action)
        except sources.ERRORS as error:
            sources.scan_untrusted(source, work_root, protected)
            errors[key] = type(error).__name__+': '+str(error)
    if request['scope'] in ('deployment', 'deployment_precision') and 'preparation' in request['stages']:
        prep = values.get('preparation', {})
        if prep.get('purpose') != 'deployment' or 'dataset' in prep.get('tasks', {}):
            errors['preparation'] = '仅部署准备须为 deployment 且不包含数据任务'
        if 'migration' in errors:
            errors['preparation'] = '迁移输入无效，没有有效部署消费者；未启动准备'
    try:
        bindings.validate(request, values)
    except sources.ERRORS as error:
        for key in request['stages']:
            errors[key] = '交接绑定无效：'+str(error)
    return values, errors, protected


def scan_prepare(value, work_root, protected):
    for item in value.get('tasks', {}).values():
        if not isinstance(item, dict) or not isinstance(item.get('request'), str):
            continue
        path = Path(item['request'])
        protected.append(path)
        try:
            sources.scan(sources.read(path), work_root, protected)
        except sources.ERRORS:
            continue  # 原阶段按任务隔离此项输入失败。



def stage_scope(key, value, action):
    if key == 'precision':
        precision_scope(value, action)
    if key == 'migration':
        if 'preparation' in value:
            raise Blocked('迁移 preparation 与总入口交接重复声明')
        verify = sources.bound(value['verify'])
        service = verify.get('service', {})
        if not isinstance(service, dict):
            raise Blocked('迁移 service 须为对象')
        if service.get('retention', 'keep') != 'keep':
            raise Blocked('部署须保留服务直到消费者结束，不允许内层 stop 提前停止')


def stage_source(key, node):
    if key in ('preparation', 'dataset_preparation'):
        return prepare_source(node)
    source = node.get('request', node.get('result'))
    value = sources.bound(source)
    if key == 'migration' and 'result' in node:
        receipts.validate(value, Path(source['path']), key, STAGES[key][0])
        from .continuation import reusable
        reusable(value)
        if value['status'] != 'completed':
            raise Blocked('仅复用已完成且功能通过的迁移；请显式适配或语义复核后续接')
    return value
