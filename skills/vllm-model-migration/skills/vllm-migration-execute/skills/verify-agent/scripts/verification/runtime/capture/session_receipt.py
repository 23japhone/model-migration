"""封存一次正式 HTTP 调用及其实际接受输出；不补发请求。"""
import json

from ...evidence import read_bound, sha256, write_json
from .adapters.state import read
from .cache_evidence import receipt_status
from .index import prediction, native_forward
from .tensors import file_ref


def http_bytes(ref):
    from pathlib import Path
    path = Path(ref['path'])
    if file_ref(path, path.parent)['sha256'] != ref['sha256']: raise ValueError('HTTP 原件摘要变化')
    return path.read_bytes()


def http_json(ref):
    return json.loads(http_bytes(ref))


def seal(request, authority, handoff, root, lease, ready):
    if lease['state'] != 'armed' or lease['trace_id'] != request['trace_id']:
        raise ValueError('没有匹配的活动请求')
    folder = root / lease['directory']; intent = read(folder / 'intent.json')
    if 'rank_workers' in ready:
        from .parallel.receipt import seal_parallel
        from .generation.delivery import load_response
        response = load_response(request['http'], partial=True)
        if 'error' in response:
            from .parallel.failure import inference_failed
            return inference_failed(request, folder, intent, response)
        return seal_parallel(request, authority, handoff, root, folder, intent, ready)
    return seal_single(request, authority, handoff, root, folder, intent, ready)


def seal_single(request, authority, handoff, root, folder, intent, ready):
    errors = list(folder.glob('error-*.json'))
    if errors: raise ValueError(read(errors[0])['error'])
    mapped, engine, response = mapped_response(folder, request)
    sampling = http_json(request['http']['request'])
    tokens = engine['token_ids']
    accepted = [read(p) for p in sorted(folder.glob('accepted-*.json'), key=lambda p: int(p.stem.split('-')[-1]))]
    if tokens != [r['token_id'] for r in accepted] or [r['output_index'] for r in accepted] != list(range(len(tokens))):
        raise ValueError('引擎接受序列与服务实际返回的输出序列不一致')
    # 包括未产生 Token 的分段 prefill：每个开启的前向都必须闭合。
    forwards = completed_forwards(folder)
    rows = [prediction(folder, root, ready, mapped, tokens, i) for i in range(len(tokens))]
    extra = complete_coverage(folder, root, ready, rows, forwards)
    write_json(folder / 'sampling.json', sampling); write_json(folder / 'response.json', response)
    cache = receipt_status(folder, intent, tokens)
    if 'cache_capture' in intent:
        returned = response['choices'][0].get('token_ids')
        if returned is not None and returned != tokens:
            raise ValueError('HTTP 输出 Token 与引擎接受序列不同')
        from .timeline import seal_timeline
        cache = seal_timeline(folder, mapped, tokens, rows, extra, intent)
    receipt = {**cache, 'request_id': request['trace_id'], 'sample_key': intent['sample_key'], 'native_id': intent['native_id'],
               'source': authority['source'], 'preparation': handoff['worker_capture']['receipt'],
               'derived_config': authority['derived_config'], 'token_ids': tokens, 'outputs': rows,
               'sampling': file_ref(folder / 'sampling.json', folder), 'response': file_ref(folder / 'response.json', folder),
               'http': request['http'], 'engine_input': file_ref(folder / 'request.json', folder),
               'frontend': file_ref(folder / 'frontend.json', folder), 'engine_output': file_ref(folder / 'engine-output.json', folder),
               'response_id': response['id'], 'finish_reason': engine['finish_reason'],
               'worker': file_ref(root / 'worker.json', root), 'engine': file_ref(root / 'engine.json', root),
               'forwards': [file_ref(p, folder) for p in forwards], 'extra_forwards': extra}
    path = folder / 'capture-receipt.json'; write_json(path, receipt)
    status = 'incomplete' if cache.get('cache_status') == 'incomplete' else 'completed'
    return {**cache, 'status': status, 'trace_id': request['trace_id'], 'sample_key': intent['sample_key'],
            'native_id': intent['native_id'], 'receipt': file_ref(path, folder), 'token_count': len(tokens)}


def mapped_response(folder, request):
    mapped = read(folder / 'request.json'); frontend = read(folder / 'frontend.json')
    from .generation.delivery import load_response
    response = load_response(request['http'], partial=True)
    incomplete_stream = 'delivery' in response and not response['delivery']['complete']
    engine_file = folder / 'engine-output.json'
    if incomplete_stream and not engine_file.exists():
        ended = read(folder / 'frontend-end.json')
        if ended['trace_id'] != request['trace_id']:
            raise ValueError('断流请求尚未关闭')
        # No frontend engine result was emitted. The scheduler ledger remains authoritative.
        engine = {'external_id': mapped['external_id'], 'prompt_ids': mapped['prompt_ids'],
                  'token_ids': [], 'finished': False, 'finish_reason': None}
    else:
        engine = read(engine_file)
    response_matches = response['id'] == frontend.get('response_id') or incomplete_stream and response['id'] is None
    if (frontend['trace_id'] != request['trace_id'] or not response_matches
            or mapped['external_id'] != frontend['external_id'] or engine['external_id'] != mapped['external_id']
            or mapped['prompt_ids'] != engine['prompt_ids'] or len(response['choices']) != 1):
        raise ValueError('正式请求、前端、引擎映射或实际前文不一致')
    return mapped, engine, response


def completed_forwards(folder):
    begins, ends = list(folder.glob('begin-*.json')), list(folder.glob('end-*.json'))
    if not begins or {p.stem[6:] for p in begins} != {p.stem[4:] for p in ends}:
        raise ValueError('前向开始/结束记录缺失')
    for path in ends:
        if read(path).get('status') != 'completed': raise ValueError('前向未成功完成：' + path.name)
    return [*begins, *ends]


def require_coverage(value):
    if not value or value.get('status') != 'complete_within_scope':
        raise ValueError('缺少完整的实际调用与张量覆盖核验')


def extra_details(folder, frame, ready, calls):
    intent = read(folder / 'intent.json')
    if 'cache_capture' not in intent:
        return {}
    from .cache_evidence import from_frame
    return {**from_frame(folder, frame, intent, ready, calls), 'output_index': None,
            'scheduled_output_index': frame['output_index'], 'internal_id': frame['internal_id'],
            'client_request_id': intent['request_id'], 'rank': ready['rank'], 'step': frame['step'],
            'positions': frame['positions'], 'input_ids': frame['input_ids'], 'layers': ready['layers'],
            'architecture': ready['architecture']}


def complete_coverage(folder, root, ready, rows, forwards):
    indexed = [read_bound(ref) for ref in rows]
    for row in indexed: require_coverage(row.get('coverage'))
    known = {row['forward_id'] for row in indexed}; result = []
    for path in forwards:
        if not path.name.startswith('end-'): continue
        frame = read(path)
        if frame['forward_id'] in known: continue
        dump, calls, coverage = native_forward(root, ready, frame, 0)
        require_coverage(coverage)
        # 未产出 Token 的前向没有预测行，引用其完整张量。
        for call in calls:
            for side in call['tensors'].values():
                for item in side: item['slice'] = 'whole_tensor'
        dest = folder/('forward-tensors-'+frame['forward_id']+'.json')
        details = extra_details(folder, frame, ready, calls)
        write_json(dest, {**details, 'forward_id': frame['forward_id'], 'coverage': coverage, 'calls': calls,
                         'native_metadata': [file_ref(dump/n, dump) for n in ('dump.json', 'construct.json')]})
        result.append(file_ref(dest, folder))
    return result
