"""仅补充第三遍证据或容差时，沿用固定的单题首差和上次交付状态。"""
from copy import deepcopy
from pathlib import Path

from coordination.runtime import sources
from ..state import identity
from . import stage


def context_identity(supplied):
    return identity({k: v for k, v in supplied.items() if k not in ('evidence', 'task_source')})


def has_bound_token(previous):
    reports = [row['report'] for row in previous.get('task_quality', []) if row.get('report')]
    return any(row.get('quality_report') in reports and stage.token_quality(row['token_report']) == row['quality_report']
               for row in previous.get('token_alignment', []) if row.get('token_report'))


def can_resume(previous, task, supplied):
    accuracy = task['accuracy']
    old_policy = previous.get('declared_inputs', previous['inputs'])['accuracy'].get('tensor_alignment')
    new_evidence = [item for item in supplied['evidence'] if item not in previous.get('tensor_context_evidence', [])]
    if previous.get('status') not in ('blocked', 'completed'):
        return False
    if not new_evidence and old_policy == accuracy.get('tensor_alignment'):
        return False
    return (previous.get('tensor_context_identity') == context_identity(supplied)
            and has_bound_token(previous)
            and ('tensor_alignment' in accuracy or any(stage.has_first_difference(row)
                 for row in previous.get('token_alignment', [])))
            and handoff_only(previous, supplied))


def handoff_only(old, supplied):
    # 此处只识别交接类型。张量阶段独立验摘要，错误引用记入其缺项。
    known = old.get('tensor_context_evidence', [])
    for item in supplied['evidence']:
        if item in known:
            continue
        if Path(item['path']).suffix.lower() != '.json':
            return False
        try:
            info = sources.read(item['path'])
        except sources.ERRORS:
            return False
        if info.get('format') not in ('tensor-bundle-v1', 'tensor-scope-v1', 'tensor-inventory-v1', 'tensor-plan-v1'):
            return False
    return True


def check_delivery(old):
    delivery = sources.bound(old['steps']['delivery']['receipt'])
    if delivery['status'] != 'ready' or delivery['deliverables'] != old['deliverables']:
        raise ValueError('已发布的三文件交付与客户回执不一致')
    for source in delivery['deliverables'].values():
        sources.bound(source)


def resume(session, task, supplied, work_root, guarded):
    session.value['tensor_context_evidence'] = supplied.get('evidence', [])
    session.value['tensor_context_identity'] = context_identity(supplied)
    if session.tensor_handoff is not True:
        return None
    old = session.previous
    if old['status'] == 'completed':
        check_delivery(old)
    for field in ('task_quality', 'token_alignment', 'effective_inputs', 'delivery_supported'):
        if field in old:
            session.value[field] = deepcopy(old[field])
    if 'effective_inputs' in session.value:
        effective = session.value['effective_inputs']['accuracy']
        effective.pop('tensor_alignment', None)
        if 'tensor_alignment' in task['accuracy']:
            effective['tensor_alignment'] = deepcopy(task['accuracy']['tensor_alignment'])
    stage.compare_all(session, task['accuracy'].get('tensor_alignment'), supplied, work_root, guarded)
    details = {key: old[key] for key in ('deliverables', 'export_manifest', 'next_action', 'diagnostic', 'uncovered')
               if key in old}
    return session.finish(old['status'], **details)
