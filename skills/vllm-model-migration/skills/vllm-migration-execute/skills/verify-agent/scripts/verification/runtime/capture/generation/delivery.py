"""Read the actual HTTP entity, retaining the event-to-token mapping."""
import json


def event_choice(chunk):
    choices = chunk['choices']
    if len(choices) > 1 or choices and choices[0]['index'] != 0:
        raise ValueError('SSE choice differs from the single requested sequence')
    choice = choices[0] if choices else {}
    ids = choice.get('token_ids') or []
    if not isinstance(ids, list) or any(type(i) != int or i < 0 for i in ids):
        raise ValueError('invalid SSE token_ids')
    return choice, ids


def events(raw):
    # Incomplete final events, including partial UTF-8 code points at disconnect, were never dispatched.
    blocks = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n').split(b'\n\n')
    for block in blocks[:-1]:
        data = '\n'.join(line[5:].lstrip(' ') for line in block.decode('utf-8').splitlines() if line.startswith('data:'))
        if data:
            yield data


def parse_stream(raw):
    records, tokens, identity = [], [], None
    finished = None
    done = False
    for data in events(raw):
        if done:
            raise ValueError('data after SSE [DONE]')
        if data == '[DONE]':
            done = True
            continue
        chunk = json.loads(data)
        if 'error' in chunk:
            return {'error': chunk['error'], 'delivery': {'events': records, 'complete': False}}
        current = chunk['id'], chunk['model']
        if identity is not None and identity != current:
            raise ValueError('SSE response identity changed')
        identity = current
        choice, ids = event_choice(chunk)
        if finished and chunk['choices']:
            raise ValueError('SSE choice after finish_reason')
        start = len(tokens)
        tokens.extend(ids)
        finished = choice.get('finish_reason') or finished
        records.append({'event_index': len(records), 'output_indices': list(range(start, len(tokens))),
                       'token_ids': ids, 'delta': choice.get('delta'), 'text': choice.get('text'),
                       'finish_reason': choice.get('finish_reason'), 'usage': chunk.get('usage')})
    if identity is None:
        if done:
            raise ValueError('SSE [DONE] without response identity')
        identity = None, None
    return {'id': identity[0], 'model': identity[1], 'choices': [{'token_ids': tokens, 'finish_reason': finished}],
            'delivery': {'events': records, 'done': done, 'complete': bool(done and finished)}}


def load_response(http, *, partial=False):
    from ..session_receipt import http_bytes, http_json
    request = http_json(http['request'])
    raw = http_bytes(http['response'])
    if request.get('stream') and not raw.lstrip().startswith(b'{'):
        try:
            return parse_stream(raw)
        except (KeyError, TypeError, ValueError) as error:
            if not partial:
                raise
            # Recover only the contiguous prefix of fully dispatched events that validates.
            return verified_prefix(raw, error)
    result = json.loads(raw)
    if request.get('stream') and 'error' not in result:
        if partial:
            return verified_prefix(b'', '流式请求缺少 SSE 响应原件')
        raise ValueError('流式请求缺少 SSE 响应原件')
    return result


def verified_prefix(raw, error):
    blocks = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n').split(b'\n\n')[:-1]
    left, right = 0, len(blocks)
    result = parse_stream(b'')
    while left < right:
        length = (left + right + 1) // 2
        try:
            prefix = parse_stream(b'\n\n'.join(blocks[:length]) + b'\n\n')
        except (KeyError, TypeError, ValueError):
            right = length - 1
        else:
            result, left = prefix, length
    result['delivery'].update(complete=False, error=str(error))
    return result
