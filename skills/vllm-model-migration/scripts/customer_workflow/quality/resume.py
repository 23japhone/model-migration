"""续接先复核本次原生测量候选，再沿用已冻结并被 Token 预测包引用的质量报告。"""
from coordination.runtime import sources
from ..tensor_alignment import same_source


def previous(session, entry):
    old = session.previous
    if not isinstance(old, dict) or old['run_id'] != session.value['run_id']:
        return None
    if old.get('task_source') != session.value['task_source'] and not same_source(
            old, session.value['task_source'], session.value['inputs']):
        return None
    choices = [item for item in old.get('task_quality', []) if
               (item.get('step'), item.get('dataset')) == (entry['step'], entry['dataset'])]
    if len(choices) != 1:
        return None
    candidate = choices[0]
    # 经过多次续接时，steps 保存最新复核步骤，stage 始终指向原始冻结报告的所属阶段。
    origin = candidate.get('verification_stage', candidate.get('stage'))
    if old['steps'][entry['step']].get('receipt') != origin:
        return None
    return candidate


def artifacts(entry, run_id):
    stage = sources.bound(entry['stage'])
    report = sources.bound(entry['report'])
    if (stage.get('task_id'), stage.get('operation'), stage.get('status'), stage.get('run_id')) != (
            'vllm-precision-align', 'quality', 'completed', run_id):
        raise ValueError('质量阶段身份不符')
    if stage['quality_report'] != entry['report'] or stage['receipt'] != report['evidence']['receipt']:
        raise ValueError('质量报告没有绑定到当前候选')
    if report.get('task_id') != 'vllm-precision-compare' or report.get('status') != 'completed':
        raise ValueError('缺少已完成质量比较')
    if report.get('comparison_basis') == 'customer_provided':
        if report['baseline'] != stage['inputs']['baseline']:
            raise ValueError('质量报告与阶段请求的客户 GPU 基线不同')
    else:
        source = stage['inputs']['gpu_evidence']
        if report['evidence'].get('gpu_evidence') != source:
            raise ValueError('首轮报告与 GPU 原生评测来源不同')
        sources.bound(source)
    sources.bound(stage['receipt'])
    return stage, report


def same_measurement(a, b):
    first = a['inputs']
    current = b['inputs']
    if a['receipt'] != b['receipt'] or b.get('source') != 'reused':
        return False
    keys = ['baseline', 'native_request', 'baseline_source']
    if current.get('baseline_source') == 'publisher':
        keys.append('gpu_evidence')
    if any(first.get(key) != current.get(key) for key in keys):
        return False
    verification = sources.bound(b['verification'])
    if (verification.get('task_id'), verification.get('operation'), verification.get('status')) != (
            'verify-agent-plus', 'quality-analyze', 'completed'):
        return False
    return (verification.get('conclusions', {}).get('evaluation_validity') == 'pass'
            and verification['inputs']['source'] == a['receipt']
            and verification['inputs']['expected'] == current['native_request'])


def frozen(session, entry):
    try:
        old = previous(session, entry)
        if not old:
            return entry
        start, first = artifacts(old, session.value['run_id'])
        current, latest = artifacts(entry, session.value['run_id'])
        if not same_measurement(start, current):
            return entry
        if current['verification'] != latest['evidence'].get('verification'):
            return entry
        if first.get('conclusions', {}).get('precision_target') != 'fail':
            return entry
        content = ('baseline', 'scores', 'conclusions', 'next_action', 'uncovered')
        if any(first.get(key) != latest.get(key) for key in content):
            return entry
    except sources.ERRORS:
        # 最新测量已独立交付；无法核验原回执时，继续使用本次新报告。
        return entry
    return {**entry, 'stage': old['stage'], 'report': old['report'], 'verification_stage': entry['stage']}


def prior_candidate(session, key, dataset, baseline, native, gpu):
    try:
        saved = previous(session, {'step': key, 'dataset': dataset})
        if saved:
            stage, report = artifacts(saved, session.value['run_id'])
            wanted = {'baseline': baseline, 'native_request': native}
            if stage['inputs'].get('baseline_source') == 'publisher':
                wanted['gpu_evidence'] = gpu
            if (report['conclusions']['precision_target'] == 'fail'
                    and all(stage['inputs'].get(k) == v for k, v in wanted.items())):
                return stage['receipt']
    except sources.ERRORS:
        return None
    return None
