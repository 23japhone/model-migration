"""EvalScope 文本投影、输出预算结束证据及截断记录；不参与评分。"""
import re

from ...http_evidence import one_choice, text_parts, usage
from ...http_evidence import validate_response as check_response
from .binding import messages_identity


def http_choice(value):
    choice = one_choice(value)
    if choice.get('finish_reason') not in ('stop', 'length'):
        raise ValueError('EvalScope 生成未正常结束或不是输出长度上限')
    return choice


def http_prediction(value):
    content, reasoning = text_parts(http_choice(value))
    if not isinstance(content, str) or (reasoning is not None and not isinstance(reasoning, str)):
        raise ValueError('EvalScope 仅支持文本响应')
    if not reasoning:
        # 与原生 ChatMessageAssistant 投影一致，仅核验，不改评分输入。
        match = re.search(r'<think(?:\s+signature="([^"]*)")?(?:\s+redacted="(true)")?\s*>(.*?)</think>', content, re.DOTALL)
        if match:
            return (content[:match.start()] + content[match.end():]).strip(), match.group(3).strip()
    return content, reasoning or ''


def validate_response(raw, status):
    # 记录时允许保存 length；reader 再与请求上限、上下文及原生结束原因核对。
    return check_response(raw, status, http_prediction)


def http_record(body, response):
    reason = http_choice(response)['finish_reason']
    if reason == 'length':
        maximum = body.get('max_tokens', body.get('max_completion_tokens'))
        if type(maximum) != int or maximum <= 0 or usage(response)['completion_tokens'] != maximum:
            raise ValueError('生成截断但未达到请求配置的输出上限')
    return messages_identity(body['messages']), http_prediction(response), reason


def truncation_summary(predicted, reviewed, fingerprint):
    scores = {row['id']: row['sample_score']['score']['value'] for row in reviewed}
    samples = [{'sample_id': row['id'], 'stop_reason': 'max_tokens',
                'finish_reason': 'length' if fingerprint is not None else None,
                'native_score': scores.get(row['id'])}
               for row in predicted if row['model_output']['choices'][0]['stop_reason'] == 'max_tokens']
    return {'count': len(samples), 'sample_ids': [row['sample_id'] for row in samples],
            'samples': samples, 'evidence_verified': fingerprint is not None}
