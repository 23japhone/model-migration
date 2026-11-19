"""第 1 步：原始请求静态校验，纯本地检查，不访问服务。"""


def _check_messages(messages):
    bad = []
    for index, message in enumerate(messages):
        if (not isinstance(message, dict)
                or message.get('role') not in ('system', 'user', 'assistant', 'tool')
                or not message.get('content')):
            bad.append('第 %d 条 role/content 异常' % index)
    if bad:
        return {'name': 'messages 结构', 'level': 'FAIL', 'detail': '; '.join(bad)}
    return {'name': 'messages 结构', 'level': 'PASS', 'detail': ''}


def _check_tools(tools):
    if not isinstance(tools, list) or not tools:
        return {'name': 'tools 非空列表', 'level': 'FAIL', 'detail': 'tools 为空或非列表'}
    bad, seen = [], {}
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            bad.append('第 %d 个工具不是对象' % index)
            continue
        fn = tool.get('function') or {}
        if tool.get('type') != 'function' or not fn.get('name') \
                or not isinstance(fn.get('parameters', {}), dict):
            bad.append('第 %d 个工具缺 type/function.name/parameters' % index)
        seen[fn.get('name')] = seen.get(fn.get('name'), 0) + 1
    duplicate = [name for name, count in seen.items() if name and count > 1]
    if duplicate:
        bad.append('工具名重复: %s' % ', '.join(map(str, duplicate)))
    if bad:
        return {'name': 'tools JSON 结构', 'level': 'FAIL', 'detail': '; '.join(bad)}
    return {'name': 'tools JSON 结构', 'level': 'PASS', 'detail': ''}


def step1_check_request(req):
    """返回 (items, fatal)。fatal=True 时终止（messages 缺失/为空）。"""
    items = []
    messages = req.get('messages')
    if not isinstance(messages, list) or not messages:
        items.append({'name': 'messages 存在且非空', 'level': 'FAIL', 'detail': 'messages 缺失或为空'})
        return items, True
    items.append(_check_messages(messages))

    tools = req.get('tools')
    if tools is not None:
        items.append(_check_tools(tools))

    tool_choice = req.get('tool_choice')
    if tool_choice is not None and tool_choice not in ('auto', 'none', 'required') \
            and not (isinstance(tool_choice, dict) and tool_choice.get('type') == 'function'):
        items.append({'name': 'tool_choice 取值', 'level': 'WARN', 'detail': '非常规取值: %s' % tool_choice})

    temperature = req.get('temperature')
    if isinstance(temperature, (int, float)) and temperature > 1:
        items.append({'name': '采样参数合理性', 'level': 'WARN',
                      'detail': 'temperature=%s 偏高, 定界场景建议 0(修改前先确认仍能复现同一问题)' % temperature})
    return items, False


def step1_has_fail(items):
    return any(item['level'] == 'FAIL' for item in items)
