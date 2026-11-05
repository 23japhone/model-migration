"""完整SSE组装；错误、截断、空生成与结束后数据均不得通过。"""
import json

from .usage import validate as validate_usage


def consume(state, body, model):
    if state['done']:raise ValueError('[DONE] 后出现额外数据')
    if body == '[DONE]':
        state['done'] = True
        return
    event = json.loads(body)
    if not isinstance(event, dict) or 'error' in event or event.get('model') != model:
        raise ValueError('SSE 错误或 model 身份不一致')
    usage = validate_usage(event.get('usage'))
    if usage is not None:state['usage'] = usage
    choices = event.get('choices')
    if not isinstance(choices, list) or len(choices) > 1:raise ValueError('SSE choices 无效')
    if not choices:
        if not isinstance(event.get('usage'), dict):raise ValueError('SSE 空 choices 且无 usage')
    else:
        choice(state, choices[0])
    state['events'].append(event)


def choice(state, value):
    if not isinstance(value, dict) or not isinstance(value.get('delta', {}), dict):
        raise ValueError('SSE choice/delta 须为对象')
    if value.get('index', 0) != 0:raise ValueError('SSE 非预期的 choice index')
    text = value.get('delta', {}).get('content', value.get('text'))
    if text is not None:
        if not isinstance(text, str):raise ValueError('SSE 生成内容不是字符串')
        if state['finish_reason'] is not None and text:raise ValueError('结束后出现额外生成')
        state['content'].append(text)
    finish = value.get('finish_reason')
    if finish is not None:
        if state['finish_reason'] is not None:raise ValueError('重复 finish_reason')
        state['finish_reason'] = finish


def parse(lines, model):
    state = {'done': False, 'finish_reason': None, 'content': [], 'events': [], 'usage': None}
    pending = []
    for raw in lines:
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if pending:
                consume(state, '\n'.join(pending), model)
                pending = []
        elif line.startswith('data:'):
            pending.append(line[5:].lstrip(' '))
        elif line.startswith('event:') and line[6:].strip() == 'error':
            raise ValueError('SSE event:error')
    text = ''.join(state['content'])
    if pending or not state['done'] or state['finish_reason'] != 'stop' or not text.strip():
        raise ValueError('SSE 截断、未正常结束或生成为空')
    return {'text': text, 'finish_reason': state['finish_reason'], 'events': state['events'],
            'done': True, 'usage': state['usage']}
