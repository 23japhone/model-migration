"""对齐完成的 HTTP 响应或已由引擎证实的取消。"""
import json

from ..adapters.state import read
from ..session_receipt import http_json, http_bytes, mapped_response


def outcome(group, folder, request, intent):
    payload = http_json(request['http']['request'])
    body = read(folder / 'frontend-payload-digest.json')
    if (body['http_sha256'] != request['http']['request']['sha256'] or payload.get('temperature') != 0
            or payload.get('stream', False)):
        raise ValueError('成员 HTTP 正文身份或采样模式不符')
    abort = folder / 'engine-abort.json'
    if abort.is_file() and 'async_queue' in read(group / 'group.json'):
        return cancelled(folder, request, intent)
    if (group / 'engine-dead.json').is_file():
        from .terminal import confirmed
        raw = http_bytes(request['http']['response'])
        if confirmed(group) and (not raw or json.loads(raw).get('error')):
            mapped = frontend_identity(folder, intent)
            return mapped, accepted_tokens(folder), None, [], {'inference_status':'failed', 'delivery':'error'}
    mapped, engine, response = mapped_response(folder, request)
    choice, = response['choices']
    tokens = engine['token_ids']
    if (not engine['finished'] or choice['token_ids'] != tokens or choice['finish_reason'] != engine['finish_reason']):
        raise ValueError('HTTP 与引擎最终输出或结束原因不同')
    return mapped, tokens, engine['finish_reason'], ['engine-output'], {}


def cancelled(folder, request, intent):
    mapped = frontend_identity(folder, intent)
    if read(folder / 'engine-abort.json')['internal_id'] != mapped['internal_id']:
        raise ValueError('取消记录与引擎身份不符')
    raw = http_bytes(request['http']['response'])
    if raw and not json.loads(raw).get('error'):
        raise ValueError('引擎取消与 HTTP 响应不一致')
    return mapped, accepted_tokens(folder), 'abort', ['engine-abort'], {'delivery': 'cancelled'}


def frontend_identity(folder, intent):
    mapped = read(folder / 'request.json')
    frontend = read(folder / 'frontend.json')
    ended = read(folder / 'frontend-end.json')
    if (frontend['external_id'] != mapped['external_id'] or frontend['trace_id'] != intent['trace_id']
            or ended['trace_id'] != intent['trace_id']):
        raise ValueError('失败请求与前端或引擎身份不符')
    return mapped


def accepted_tokens(folder):
    accepted = sorted((read(p) for p in folder.glob('accepted-*.json')), key=lambda v:v['output_index'])
    return [r['token_id'] for r in accepted]
