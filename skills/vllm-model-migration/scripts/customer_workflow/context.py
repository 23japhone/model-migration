"""Agent 观察的来源与任务绑定；缺口交给 Agent 探索，不要求客户写内部请求。"""
from pathlib import Path
from coordination.runtime import sources
from coordination.request import fields, reference
from . import intent
from .contract import normalize as normalize_task
from evalscope_installation import validate, protect
from .evaluation import selection
from .tensor_alignment.continuation import can_resume


def verify_evidence(refs):
    for source in refs:
        if sources.ref(source['path']) != source:
            raise ValueError('Agent 证据文件摘要变化')


def load(path, task_source, *, defer_evidence=False):
    if path is None:
        return None
    value = sources.read(path)
    fields(value, ('schema_version', 'task_source', 'intent', 'observations', 'evidence'),
           ('migration_result', 'evaluation_results', 'runtime_environment', 'prompt_fields', 'decisions', 'evalscope_install'))
    if value['schema_version'] != '1.0' or value['task_source'] != task_source:
        raise ValueError('Agent 上下文不属于当前客户 task.json')
    intent.validate(value['intent'])
    intent.parameters(value.get('prompt_fields', {}))
    if not isinstance(value['observations'], dict) or not isinstance(value['evidence'], list):
        raise ValueError('Agent 观察与证据类型无效')
    if 'evaluator_candidates' in value['observations']:
        raise ValueError('旧 evaluator_candidates 不再支持；使用 evalscope_install')
    if 'evalscope_install' in value:
        validate(value['evalscope_install'])
    for source in value['evidence']:
        reference(source)
    if not defer_evidence:
        verify_evidence(value['evidence'])
    return value


def start(args, source, raw, parent):
    from .state import Session, load_previous
    task_source = sources.ref(source)
    if sources.bound(task_source) != raw:
        raise ValueError('客户输入在校验后变化，请重新读取任务')
    prior = sources.ref(args.resume) if args.resume else None
    supplied = load(args.context, task_source, defer_evidence=bool(prior))
    effective, conflicts = declaration(supplied, raw)
    snapshot = normalize_task(raw)
    previous = load_previous(prior, task_source, snapshot, args.run_id)
    install = None
    quality_requested = None
    if supplied is not None and not conflicts:
        quality_requested = supplied['intent']['accuracy'] or bool(effective['accuracy'].get('gpu_baselines'))
        install = preflight(supplied, effective, args, parent, previous)
    tensor_handoff = bool(previous and supplied and not conflicts and not (install and install.is_preview)
                          and can_resume(previous, effective, supplied))
    if previous and supplied and not tensor_handoff:
        verify_evidence(supplied['evidence'])
    if sources.ref(source) != task_source:
        raise ValueError('客户输入在参数合成后变化，请重新读取任务')
    session = Session(parent, source, snapshot, args.run_id, prior, effective_task=effective,
                      quality_requested=quality_requested, tensor_handoff=tensor_handoff)
    return supplied, session, effective, conflicts, install


def declaration(supplied, raw):
    if supplied is None:
        return normalize_task(raw), []
    interpreted = supplied.get('prompt_fields', {})
    combined, inferred = intent.compose(raw, interpreted)
    conflicts = supplied['intent']['conflicts'] + inferred
    if conflicts:
        return normalize_task(raw), conflicts
    return normalize_task(combined), []


def preflight(supplied, effective, args, parent, previous=None):
    roots = [Path(row['path']) for row in effective['accuracy']['datasets']]
    if isinstance(effective['model']['path'], str):
        roots.append(Path(effective['model']['path']))
    sources.protect(parent, [Path(args.context), *roots])
    plan = installation(supplied, effective, previous)
    if plan and plan.options:
        protect(plan.options, [args.context, args.work_root, supplied['task_source']['path']])
    return plan


def installation(supplied, task, previous=None):
    if task['accuracy']['evaluator'] != 'evalscope':
        if 'evalscope_install' in supplied:
            raise ValueError('evalscope_install 不适用于显式 AISBench；不自动切换后端')
        return None
    plan = selection.resolve(supplied.get('evalscope_install', {}), previous)
    roots = [row['path'] for row in task['accuracy']['datasets']]
    if isinstance(task['model']['path'], str):
        roots.append(task['model']['path'])
    if plan.options:
        protect(plan.options, roots)
    return plan


def next_actions(missing):
    return {'owner': 'agent', 'missing_observations': sorted(set(missing)),
            'instruction': '继续读取客户流程契约并调用对应公开发现/准备入口，收集实际证据后续接；'
                           '内部观察不能转交客户填写。仅业务冲突、必要身份缺失或容器删除需询问用户。'}
