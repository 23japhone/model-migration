"""按质量报告续接第三遍，把已交付的逐题证据留在客户台账中。"""
from coordination.runtime import sources
from ..invoke import execute
from ..state import identity


def source_identity(record):
    # 客户入口只索引证据；阶段负责校验包和题号，并保存逐份缺项原因。
    return identity([record.get(key) for key in ('format', 'hardware', 'model_identity', 'plan_sha256', 'case')])


def references(previous, supplied):
    """保留旧包以便只改容差。客户本次补交的同一计划同侧包覆盖旧包。"""
    def indexed(refs):
        items = {}
        for source in refs:
            try:
                value = sources.bound(source)
            except sources.ERRORS:
                continue
            if value.get('format') in ('tensor-scope-v1', 'tensor-inventory-v1', 'tensor-bundle-v1', 'tensor-plan-v1'):
                group = items.setdefault(source_identity(value), [])
                if source not in group:
                    group.append(source)
        return items

    old = indexed(previous)
    old.update(indexed(supplied))
    # 当前输入也包含非标准/损坏的交付，传入阶段产生可追溯的缺项原因。
    result = list(supplied)
    for items in old.values():
        result.extend(source for source in items if source not in result)
    return result


def previous_sources(session, key, token):
    step = (session.previous or {}).get('steps', {}).get(key, {})
    if not step.get('receipt'):
        return []
    stage = sources.bound(step['receipt'])
    if (stage.get('operation'), stage.get('inputs', {}).get('quality_report')) != ('tensor', token['quality_report']):
        return []
    return [*stage['inputs']['evidence'], *stage.get('plans', []), *stage.get('npu_bundles', [])]


def compare_one(session, key, token, policy, supplied, root):
    if not token.get('token_report'):
        return {'status': 'not_triggered' if token.get('status') == 'not_triggered' else 'insufficient_evidence',
                'reason': '没有可供第三遍定位的单题 Token 比较报告', 'quality_report': token.get('quality_report')}
    quality = token['quality_report']
    evidence = references(previous_sources(session, key, token), supplied.get('evidence', []))
    request = {'schema_version': '1.0', 'quality_report': quality, 'token_report': token['token_report'],
               'tensor_alignment': policy, 'evidence': evidence}
    if token.get('stage'):
        origin = sources.bound(token['stage'])
        if origin.get('token_report') != request['token_report'] or origin['inputs']['quality_report'] != quality:
            raise ValueError('二遍阶段回执与当前任务来源不同')
        if 'native_request' in origin['inputs']:
            request['native_request'] = origin['inputs']['native_request']
    elif token.get('quality_stage'):
        origin = sources.bound(token['quality_stage'])
        if origin.get('quality_report') != quality or origin.get('operation') != 'quality':
            raise ValueError('冻结的原生采集请求与质量报告不同')
        request['native_request'] = origin['inputs']['native_request']
    result = execute(session, key, 'vllm-precision-align', request, root, action='tensor')
    return {**{k: result[k] for k in ('status', 'alignment_status', 'comparison_result', 'simulated', 'tensor_report', 'plans') if k in result},
            'stage': session.value['steps'][key]['receipt'], 'quality_report': quality, 'token_report': token['token_report']}


def token_quality(source):
    try:
        info = sources.bound(source)
    except sources.ERRORS:
        return None
    if ((info.get('task_id'), info.get('operation')) == ('vllm-precision-align', 'token')
            and isinstance(info.get('cases'), list)):
        return info.get('quality_report')
    return None


def external_tokens(quality_rows, supplied):
    """已有 Token 报告可直接随上下文交付，仍逐份绑定本轮质量报告。"""
    reports = [(source, token_quality(source)) for source in supplied.get('evidence', [])]
    rows = []
    for row in quality_rows:
        quality = row.get('report')
        choices = [source for source, origin in reports if quality and origin == quality]
        if len(choices) == 1:
            rows.append({'status': 'completed', 'quality_report': quality, 'token_report': choices[0],
                         'quality_stage': row.get('stage')})
        else:
            rows.append({'status': 'insufficient_evidence', 'quality_report': quality})
    return rows


def compare_all(session, policy, supplied, root, guarded):
    results = []
    session.value['tensor_alignment'] = results
    tokens = session.value.get('token_alignment')
    if not tokens:
        tokens = external_tokens(session.value.get('task_quality', []), supplied)
        session.value['token_alignment'] = tokens
    for index, token in enumerate(tokens):
        key = f'tensor-{index}'
        row = guarded(session, key, lambda: compare_one(session, key, token, policy, supplied, root))
        results.append(row or {'status': 'blocked', 'step': key, 'quality_report': token.get('quality_report'),
                               'token_report': token.get('token_report')})
    if not tokens:
        results.append({'status': 'insufficient_evidence', 'reason': '等待任务质量报告和二遍 Token 首异常'})
    session.save()
