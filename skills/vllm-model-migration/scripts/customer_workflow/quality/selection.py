"""质量任务的 GPU 基线选择与缺项说明，客户显式声明优先。"""
from coordination.runtime import sources


def observation(session, observations, supplied):
    value = {**observations, 'evidence': supplied.get('evidence', [])}
    try:
        migration = session.ready('migration') or {}
        value['deployment_receipt'] = (migration.get('review_revalidated')
                                       or migration.get('tasks', {}).get('verify', {}).get('receipt'))
    except sources.ERRORS as error:
        value['deployment_error'] = str(error)
    return value


def unresolved(session, key, missing, evidence=None, dataset=None):
    result = {'status': 'not_evaluated', 'reason': '无法评判是否精度对齐', 'missing': missing,
              'conclusions': {'comparability': 'not_evaluated', 'precision_target': 'not_evaluated'},
              'quality_measurement_started': False}
    if evidence:
        result['discovery'] = evidence
    if dataset:
        result['dataset'] = dataset
    report = session.artifact(key + '-not-evaluated', result)
    session.step(key, 'completed', receipt=report)
    return {**result, 'report': report, 'step': key}


def discover(task, observations, root):
    from .baseline_search import discover as find
    return find(task, observations, root)


def select(session, task, observations, supplied):
    declared = task['accuracy'].get('gpu_baselines')
    if declared:
        return [{'baseline': row} for row in declared], []
    if not supplied.get('intent', {}).get('accuracy'):
        return [], []
    try:
        found = discover(task, observations, session.parent / 'quality' / 'baseline-search')
    except (*sources.ERRORS, RuntimeError) as error:
        found = {'status': 'not_evaluated', 'baselines': [], 'sources': [], 'missing': [str(error)]}
    evidence = session.artifact('quality-baseline-search', found)
    session.value['gpu_baseline_search'] = evidence
    session.step('quality-baseline-search', 'completed', receipt=evidence)
    if found.get('status') == 'found' and found.get('baselines'):
        return [{**row, 'discovery': evidence} for row in found['baselines']], []
    missing = found.get('missing') or ['缺少可核验的同模型 GPU 评测基线']
    return [], [unresolved(session, 'quality-alignment', missing, evidence)]


def mismatches(candidate, protocol):
    """上游 GPU 与本任务可编译的评测条件必须相同，才为自动基线分配模型推理。"""
    from .inputs import normalize_one
    normalized = normalize_one(candidate['baseline'])
    source = candidate['protocol']
    keys = ('dataset', 'dataset_revision', 'dataset_identity', 'model_identity', 'task_name', 'task_revision',
            'evaluator', 'evaluator_version', 'subsets', 'split', 'sample_identities',
            'prompt_config', 'generation_config', 'scoring_config')
    issues = [key for key in keys if source.get(key) is None or source[key] != protocol.get(key)]
    for item in normalized['scores']:
        metric = item['metric']
        old = source.get('metrics', {}).get(metric)
        if not old or old != protocol.get('metrics', {}).get(metric):
            issues.append('metrics.' + metric)
    return issues
