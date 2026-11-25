"""为每份 GPU 基线准备独立测量，并把质量报告绑定到客户回执。"""
from time import monotonic

from coordination.runtime import sources
from .. import recovery
from ..invoke import execute
from . import selection


def recent_measurements(work_root, dataset):
    """仅发现精度阶段公布的同数据集质量回执；最终身份由原生历史分析核验。"""
    pattern = '*/vllm-precision-align/quality/*/quality-stage-manifest.json'
    for path in sorted(work_root.glob(pattern)):
        try:
            item = sources.read(path)
            if (item.get('task_id') == 'vllm-precision-align' and item.get('receipt')
                    and item['inputs']['baseline']['dataset'] == dataset):
                yield item['receipt']
        except sources.ERRORS:
            continue


def unique(references):
    seen = set()
    for item in references:
        key = item['path'], item['sha256']
        if key not in seen:
            seen.add(key)
            yield item


def run_one(session, task, observed, supplied, work_root, candidate, index):
    from . import prepare
    baseline = candidate['baseline']
    key = f'quality-{index}'
    started = monotonic()
    try:
        if not observed.get('deployment_receipt'):
            raise ValueError('质量测量等待已验证部署回执')
        public_gpu = candidate['gpu_evidence'] if 'discovery' in candidate else None
        plan = prepare(baseline, task, observed, session.parent / 'quality' / key, public_gpu=public_gpu)
        if 'discovery' in candidate:
            issues = selection.mismatches(candidate, plan['protocol'])
            if issues:
                raise ValueError('自动基线与 NPU 条件存在差异：' + '、'.join(issues))
            static = candidate.get('static_conditions')
            if not static:
                raise ValueError('自动基线缺少静态模板与生成配置依据')
            plan['native_request']['expected_static_conditions'] = static
    except (*sources.ERRORS, RuntimeError) as error:
        if 'discovery' not in candidate:
            raise
        return selection.unresolved(session, key, [str(error)], candidate['discovery'], baseline['dataset'])
    session.step(key, 'prepared', preparation_seconds=monotonic() - started)
    candidates = list(plan.get('candidates', []))
    candidates.extend(recent_measurements(work_root, baseline['dataset']))
    candidates.extend(supplied.get('evaluation_results', {}).values())
    candidates.extend(recovery.evaluations(session.value).values())
    request = {'schema_version': '1.0', 'baseline': baseline, 'native_request': plan['native_request'],
               'candidates': list(unique(candidates))}
    gpu = candidate.get('gpu_evidence') or plan.get('gpu_evidence')
    if gpu:
        request['gpu_evidence'] = gpu
    return execute(session, key, 'vllm-precision-align', request, work_root, action='quality')


def compare_all(session, task, observations, supplied, work_root, guarded):
    if not task['accuracy'].get('gpu_baselines') and not supplied.get('intent', {}).get('accuracy'):
        return
    observed = selection.observation(session, observations, supplied)
    choices, reports = selection.select(session, task, observed, supplied)
    for index, candidate in enumerate(choices):
        key = f'quality-{index}'
        result = guarded(session, key, lambda: run_one(
            session, task, observed, supplied, work_root, candidate, index))
        if result and result.get('status') == 'not_evaluated':
            reports.append(result)
            continue
        row = session.value['steps'][key]
        entry = {'dataset': candidate['baseline']['dataset'], 'step': key, 'status': row['status']}
        if row.get('receipt'):
            entry['stage'] = row['receipt']
        if result and result.get('quality_report'):
            entry['report'] = result['quality_report']
            if result.get('alignment_status') == 'not_evaluated':
                entry['status'] = 'not_evaluated'
        reports.append(entry)
    session.value['task_quality'] = reports
    session.save()
