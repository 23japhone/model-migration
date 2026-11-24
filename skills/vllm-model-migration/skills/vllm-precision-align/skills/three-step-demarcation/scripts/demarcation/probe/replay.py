"""渲染、反解、原始生成与 chat 对照请求；保留双次生成完整证据。"""

# completions 扩展字段：尽量保留特殊 token 与停止位置（正版三步法要求）。
COMPLETIONS_EXT_FIELDS = {
    'skip_special_tokens': False,
    'include_stop_str_in_output': True,
    'spaces_between_special_tokens': False,
    'return_token_ids': True,
}

# 第 3 步双次生成的公共采样条件；completions 与 chat 需逐项一致（K6）。
GENERATION_PARAMS = ('stop', 'top_p', 'top_k', 'seed', 'frequency_penalty',
                     'presence_penalty', 'repetition_penalty', 'min_p')


def _extract_token_ids(data):
    if isinstance(data, dict):
        for key in ('tokens', 'token_ids', 'input_ids'):
            value = data.get(key)
            if isinstance(value, list) and value:
                return value
    return None


def _extract_render_token_ids(data, contract):
    if contract.get('render_response_is_list'):
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    value = item.get('prompt_token_ids')
                    if isinstance(value, list) and value:
                        return value
        if isinstance(data, dict):
            value = data.get('prompt_token_ids')
            if isinstance(value, list) and value:
                return value
        return None
    if isinstance(data, dict):
        for key in ('token_ids', 'tokens', 'input_ids'):
            value = data.get(key)
            if isinstance(value, list) and value:
                return value
    return None


def completions_ext_fields(contract):
    ext = dict(COMPLETIONS_EXT_FIELDS)
    field = contract.get('completions_token_ids_field')
    if field != 'return_token_ids':
        ext.pop('return_token_ids', None)
        if field:
            ext[field] = True
    return ext


def detokenize(client, contract, model, token_ids):
    """POST /detokenize；按契约读字段，回退 text。返回 (text, detok_resp, err)。"""
    route = contract.get('tokenize_prefix', '') + '/detokenize'
    ok, data = client.request(route, {'model': model, 'tokens': token_ids})
    if not ok:
        return None, None, 'POST %s 失败: %s' % (route, data)
    if not isinstance(data, dict):
        return None, None, 'detokenize 响应非对象'
    field = contract.get('detokenize_field', 'prompt')
    text = data.get(field)
    if not isinstance(text, str):
        text = data.get('text')
    if not isinstance(text, str):
        return None, None, 'detokenize 响应无 %s/text 字段' % field
    return text, {'model': model, 'tokens': token_ids, 'text': text}, None


def render_via_endpoint(client, contract, model, req):
    """模式 render：POST /v1/chat/completions/render 拿 token_ids，再反解。
    返回 (token_ids, text, tools_rendered, render_resp, detok_resp, err)。"""
    payload = dict(req)
    if not payload.get('model'):
        payload['model'] = model
    ok, data = client.request('/v1/chat/completions/render', payload)
    if not ok:
        if contract.get('render_requires_scale_out') and isinstance(data, str) and 'HTTP 404' in data:
            return None, None, None, None, None, \
                'render 端点存在但被关闭(0.30+ 需 --enable-scale-out 或专用模式): %s' % data
        return None, None, None, None, None, 'POST /v1/chat/completions/render 不可用: %s' % data
    token_ids = _extract_render_token_ids(data, contract)
    if token_ids is None:
        return None, None, None, data, None, \
            'render 响应无 %s 字段' % contract.get('render_token_key', 'token_ids')
    text, detok_resp, err = detokenize(client, contract, model, token_ids)
    if err:
        return token_ids, None, None, data, None, err
    return token_ids, text, True, data, detok_resp, None


def render_via_tokenize(client, contract, model, req):
    """模式 tokenize：POST /tokenize + 反解。返回
    (token_ids, text, tools_rendered, tok_resp, detok_resp, err)。"""
    payload = {'model': model, 'messages': req.get('messages', [])}
    if req.get('tools') and contract.get('tokenize_send_tools'):
        payload['tools'] = req['tools']
    if req.get('chat_template_kwargs') and contract.get('tokenize_send_chat_template_kwargs'):
        payload['chat_template_kwargs'] = req['chat_template_kwargs']
    route = contract.get('tokenize_prefix', '') + '/tokenize'
    ok, data = client.request(route, payload)
    if not ok:
        return None, None, None, None, None, 'POST %s 不可用: %s' % (route, data)
    token_ids = _extract_token_ids(data)
    if token_ids is None:
        return None, None, None, data, None, 'tokenize 响应无 tokens 字段'
    text, detok_resp, err = detokenize(client, contract, model, token_ids)
    if err:
        return token_ids, None, None, data, None, err
    return token_ids, text, None, data, detok_resp, None


def completions(client, contract, model, prompt, req, max_tokens, temperature):
    """第 3 步：直发同一组 token_ids（或文本）拿原始输出。不自动重试。
    返回 (raw_choice, response, err)。"""
    payload = {'model': model, 'prompt': prompt,
               'max_tokens': max_tokens, 'temperature': temperature}
    for key in GENERATION_PARAMS:
        if key in req:
            payload[key] = req[key]
    payload.update(completions_ext_fields(contract))
    ok, data = client.request('/v1/completions', payload)
    if not ok or not isinstance(data, dict) or not data.get('choices'):
        return None, None, 'POST /v1/completions 失败: %s' % data
    return data['choices'][0], data, None


def chat(client, model, req, max_tokens, temperature):
    """第 3 步 chat 对照：原始请求全字段透传，仅覆盖模型名与采样参数。
    返回 (chat_choice, response, err)。"""
    payload = dict(req)
    payload['model'] = model
    payload['max_tokens'] = max_tokens
    payload['temperature'] = temperature
    ok, data = client.request('/v1/chat/completions', payload)
    if not ok or not isinstance(data, dict) or not data.get('choices'):
        return None, None, 'POST /v1/chat/completions 失败: %s' % data
    return data['choices'][0], data, None
