"""JSON与SSE共同的用量结构和实际prompt覆盖门槛。"""


def validate(value):
    if value is None:return None
    if not isinstance(value, dict):raise ValueError('usage 须为对象或 null')
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ValueError('usage token 计数须为非负整数')
    return value


def require_prompt(value, minimum, expected=None):
    checked = validate(value)
    if minimum and (checked is None or checked.get('prompt_tokens', 0) < minimum):
        raise ValueError('没有达到要求的真实 prompt token 覆盖')
    if expected is not None and (checked is None or checked.get('prompt_tokens') != expected):
        raise ValueError('服务实际 prompt token 数与原生模板观察不一致')
