"""固定允许的交接字段；独立评测仅受其显式依赖影响。"""
import copy

from . import sources
from .evidence import EvidenceError, write


def validate(request, values):
    mapped = request['stages'].get('precision', {}).get('bindings', {})
    if not mapped:
        return
    if 'dataset_preparation' in values:
        data = values['dataset_preparation']
        if data.get('purpose') != 'assets' or set(data.get('tasks', {})) != {'dataset'}:
            raise ValueError('共享数据准备须为 assets 且只选择单一 dataset 任务')
    if 'precision' not in values:
        return  # 整体精度输入不可读只阻塞精度；其他明确请求的阶段继续。
    rows = values['precision'].get('evaluations', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows):
        return  # 畸形阶段输入由原公开入口拒绝，不广播为上游失败。
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids) or set(mapped)-set(ids):
        raise ValueError('精度绑定 id 不存在或评测 id 重复')
    for row in rows:
        check_conflict(row, mapped.get(row['id'], {}))


def check_conflict(row, binding):
    if not binding or 'blocked_reason' in row:
        return
    if set(binding) & set(row):
        raise ValueError('精度交接字段重复声明，禁止覆盖原始输入')
    try:
        original = sources.bound(row['request'])
    except sources.ERRORS:
        return  # 不可用单项留给执行选择时记录 blocked；不能推断其含冲突。
    if set(binding) & set(original):
        raise ValueError('精度交接字段重复声明，禁止覆盖原始输入')


def available(field, key, manifest):
    row = manifest['stages'].get(key, {})
    if row.get('status') != 'completed' or not row.get('receipt'):
        return None
    value = sources.bound(row['receipt'])
    if field == 'deployment':
        return value.get('review_revalidated', value['tasks']['verify']['receipt'])
    task = value['tasks'].get('dataset', {})
    if task.get('status') != 'completed' or task.get('conclusions', {}).get('assets') != 'pass':
        return None
    return task.get('receipt') or {'path':task['manifest'], 'sha256':task['manifest_sha256']}


def evaluation(item, mapping, manifest, attempt, index):
    if 'blocked_reason' in item:
        return item, None
    additions, missing = {}, []
    for field, dependency in mapping.items():
        source = available(field, dependency, manifest)
        if source is None:
            missing.append(dependency)
        else:
            sources.bound(source)
            additions[field] = source
    if missing:
        return None, {'status':'blocked', 'depends_on':missing, 'next_action':'resolve_dependencies'}
    value = sources.bound(item['request'])
    if set(additions) & (set(item) | set(value)):
        raise ValueError('精度交接字段重复声明')
    path = attempt/('evaluation-'+str(index)+'.request.json')
    write(path, {**value, **additions})
    return {**item, 'request':sources.ref(path)}, None


def precision(request, value, manifest, attempt):
    result = copy.deepcopy(value)
    mapped = request['stages']['precision'].get('bindings', {})
    blocked, runnable = {}, []
    for index, item in enumerate(result['evaluations']):
        if item['id'] not in mapped:
            runnable.append(item)
            continue
        try:
            row, reason = evaluation(item, mapped[item['id']], manifest, attempt, index)
        except EvidenceError:
            raise
        except sources.ERRORS as error:
            row, reason = None, {'status':'blocked', 'next_action':'provide_valid_evaluation_input',
                                 'diagnostic':{'type':type(error).__name__, 'message':str(error)}}
        if reason:
            blocked[item['id']] = reason
        else:
            runnable.append(row)
    result['evaluations'] = runnable
    if 'datasets' in result:
        used = {row['dataset'] for row in runnable if 'dataset' in row}
        result['datasets'] = {key:item for key,item in result['datasets'].items() if key in used}
    manifest['stages']['precision']['blocked_evaluations'] = blocked
    return result
