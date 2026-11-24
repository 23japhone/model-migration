"""服务端能力探测与版本契约：L1 版本号、L2 契约表、L3 能力探测兜底。"""
import re


def root_endpoint(url):
    """服务地址去掉末尾 /v1，得到根地址（vLLM 全版本的 tokenize/detokenize 在根）。"""
    value = url.rstrip('/')
    return value[:-3] if value.endswith('/v1') else value


def _parse_version(value):
    """把 '0.20.2' / '0.20.2rc1' / 'v0.20.2' 解析成 (major, minor, patch)。"""
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', value or '')
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def detect_version(client):
    """GET /version → (原始字符串, 版本元组)；失败返回 (None, None)。"""
    ok, data = client.request('/version')
    if ok and isinstance(data, dict) and data.get('version'):
        return data['version'], _parse_version(data['version'])
    return None, None


def resolve_contract(ver):
    """ver 为 (major, minor, patch) 或 None；返回各决策点的接口契约。

    ver=None 表示版本未知，取最宽松值，再由 probe_convention 补足路径与字段。
    """
    known = ver is not None
    return {
        'tokenize_prefix': '',                              # D1：vLLM 全版本在根路径
        'detokenize_field': 'prompt',                       # D2：全版本只有 prompt
        'render_supported': (not known) or ver >= (0, 16, 0),
        'render_token_key': 'prompt_token_ids' if (known and ver < (0, 18, 0)) else 'token_ids',
        'render_response_is_list': bool(known and ver < (0, 18, 0)),
        'render_requires_scale_out': bool(known and ver >= (0, 30, 0)),
        'tokenize_send_chat_template_kwargs': (not known) or ver >= (0, 6, 5),
        'tokenize_send_tools': (not known) or ver >= (0, 9, 0),
        'completions_token_ids_field': 'return_token_ids' if ((not known) or ver >= (0, 10, 2)) else None,
    }


def probe_convention(client, model):
    """版本未知时探测 tokenize/detokenize 路径前缀与 detokenize 字段名。

    只用只读、无副作用的最小请求；返回 dict（可能为空）。探测失败保持契约默认。
    """
    prefix = None
    for candidate, route in (('', '/detokenize'), ('/v1', '/v1/detokenize')):
        ok, data = client.request(route, {'model': model, 'tokens': [1, 2, 3]})
        if ok:
            prefix = candidate
            break
        if isinstance(data, str) and 'HTTP 404' not in data:  # 存在但负载被拒
            prefix = candidate
            break
    if prefix is None:
        return {}
    route = '/detokenize' if prefix == '' else '/v1/detokenize'
    ok, data = client.request(route, {'model': model, 'tokens': [1, 2, 3]})
    field = None
    if ok and isinstance(data, dict):
        if isinstance(data.get('prompt'), str):
            field = 'prompt'
        elif isinstance(data.get('text'), str):
            field = 'text'
    result = {'tokenize_prefix': prefix}
    if field:
        result['detokenize_field'] = field
    return result
