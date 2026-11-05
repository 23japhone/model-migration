"""本任务证据展示脱敏；原始资料和 SHA256 身份保持不变。"""
import re

SENSITIVE = re.compile(r'password|passwd|secret|(?:access|refresh|auth|hf)?token|(?:proxy)?authorization|credentials?|(?:x)?apikey|(?:set)?cookie|clientsecret', re.I)
URL = re.compile(r'[a-z][a-z0-9+.-]*://[^\s\"\'<>]+', re.I)
AUTH = re.compile(r'(authorization\s*[:=]\s*)[^\r\n]+', re.I)
ASSIGNMENT = re.compile(r'((?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*)[^\s,;]+', re.I)


def sensitive(key):
    key = re.split(r'=|:', str(key).lstrip('-'), maxsplit=1)[0]
    return bool(SENSITIVE.fullmatch(re.sub(r'[-_]', '', key)))


def url(match):
    # 无需解析可疑 URL；删除全部 userinfo、query、fragment，含畸形地址也不回显。
    value = re.split(r'[?#]', match.group(), maxsplit=1)[0]
    scheme, address = value.split('://', 1)
    authority, slash, path = address.partition('/')
    return scheme + '://' + authority.rsplit('@', 1)[-1] + slash + path


def text(value):
    value = AUTH.sub(r'\1[REDACTED]', value)
    value = URL.sub(url, value)
    return ASSIGNMENT.sub(r'\1[REDACTED]', value)


def arguments(values):
    result, hide_next = [], False
    for item in values:
        hidden = hide_next
        hide_next = isinstance(item, str) and item.startswith('-') and sensitive(item)
        result.append('[REDACTED]' if hidden else redact(item))
    return result


def redact(value):
    if isinstance(value, dict):
        return {text(str(key)): '[REDACTED]' if sensitive(key) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):return arguments(value)
    if isinstance(value, str):return text(value)
    return value
