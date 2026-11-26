"""按 trace 独立封存元数据，公开查询时复核来源摘要。"""
from ....evidence import write_json, read_bound
from ..adapters.state import read
from ..session_receipt import http_bytes
from .delivery import outcome
from ..tensors import file_ref
from .timeline import build


def seal(group, folder, request, authority, handoff):
    intent = read(folder / 'intent.json')
    mapped, tokens, reason, evidence, delivery = outcome(group, folder, request, intent)
    rows, refs, errors = build(group, folder, mapped, intent, tokens)
    config = read(group / 'group.json')
    profile = config.get('profile', 'batch_identity')
    if profile == 'batch_joint':
        from ..batch_joint.receipt import attach
        attach(group, folder, rows, config, errors)
    for location in (group, folder):
        for path in sorted(location.glob('error-*.json')):
            errors.append(read(path)['error'])
            refs.append(file_ref(path, location))
    for name in ('request', 'frontend', 'frontend-end', *evidence, 'frontend-payload-digest', 'identity'):
        refs.append(file_ref(folder / (name + '.json'), folder))
    refs.extend([file_ref(group / 'group.json', group), file_ref(group.parent / 'worker.json', group.parent),
                 file_ref(group.parent / 'engine.json', group.parent), handoff['worker_capture']['receipt']])
    status = 'incomplete' if errors else 'completed'
    if delivery.get('inference_status') == 'failed':
        status = 'failed'
        refs.append(file_ref(group / 'engine-dead.json', group))
    path = folder / 'capture-receipt.json'
    joint = {} if profile != 'batch_joint' else {'cache_capture': config.get('cache_capture'),
                'request_wall_seconds': read(folder / 'frontend-end.json').get('request_wall_seconds')}
    write_json(path, {**joint, **delivery, 'profile': profile, 'group_id': group.name, 'request_id': intent['trace_id'],
                      'source': authority['source'], 'sample_key': intent['sample_key'], 'native_id': intent['native_id'],
                      'status': status, 'errors': errors, 'token_ids': tokens, 'finish_reason': reason,
                      'http': request['http'], 'evidence': refs, 'timeline': rows})
    return {'status': status, 'trace_id': intent['trace_id'], 'group_id': group.name,
            'errors': errors, 'receipt': file_ref(path, folder)}


def lookup(receipt, request):
    allowed = {'source', 'request_id', 'view', 'forward_id', 'prompt_position', 'output_index'}
    if set(request) - allowed or request['request_id'] != receipt['request_id']:
        raise ValueError('批次身份查询须指定已封存成员和元数据选择器')
    for ref in receipt['evidence']:
        read_bound(ref)
    for ref in receipt['http'].values():
        http_bytes(ref)
    result = {k: receipt[k] for k in ('request_id', 'group_id', 'status', 'errors')}
    result.update({k: receipt[k] for k in ('delivery', 'inference_status') if k in receipt})
    rows = receipt['timeline']
    if request.get('view') == 'timeline':
        return {**result, 'timeline': rows, 'token_ids': receipt['token_ids']}
    return {**result, **select_row(rows, request)}


def select_row(rows, request):
    if 'forward_id' in request:
        selected = [r for r in rows if r['forward_id'] == request['forward_id']]
    elif 'prompt_position' in request:
        selected = [r for r in rows if r.get('phase') == 'prefill' and request['prompt_position'] in r['positions']]
    else:
        selected = [r for r in rows if r.get('output_index') == request.get('output_index', 1)]
    if len(selected) != 1:
        raise ValueError('该成员缺少唯一匹配的前向')
    return selected[0]
