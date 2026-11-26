"""三文件已交付且仅补充第三遍证据时，直接续接诊断。"""
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
    return (isinstance(task['accuracy'].get('tensor_alignment'), dict)
            and previous.get('status') == 'completed'
            and previous.get('tensor_context_identity') == context_identity(supplied)
            and has_bound_token(previous)
            and handoff_only(previous, supplied))


def handoff_only(old, supplied):
    # 出目录后的阶段请求保留缺失/损坏的 GPU 材料，以便单题计入缺项。
    known = old.get('tensor_context_evidence', [])
    for item in supplied['evidence']:
        if item in known:
            continue
        if Path(item['path']).suffix.lower() != '.json':
            return False
        try:
            info = sources.bound(item)
        except sources.ERRORS:
            continue
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
    if 'tensor_alignment' not in task.get('accuracy', {}):
        return None
    session.value['tensor_context_evidence'] = supplied.get('evidence', [])
    session.value['tensor_context_identity'] = context_identity(supplied)
    if not session.tensor_handoff:
        return None
    old = session.previous
    check_delivery(old)
    for field in ('task_quality', 'token_alignment', 'effective_inputs', 'delivery_supported'):
        if field in old:
            session.value[field] = deepcopy(old[field])
    if 'effective_inputs' in session.value:
        session.value['effective_inputs']['accuracy']['tensor_alignment'] = deepcopy(task['accuracy']['tensor_alignment'])
    stage.compare_all(session, task['accuracy']['tensor_alignment'], supplied, work_root, guarded)
    return session.finish('completed', deliverables=old['deliverables'], export_manifest=old['export_manifest'])
