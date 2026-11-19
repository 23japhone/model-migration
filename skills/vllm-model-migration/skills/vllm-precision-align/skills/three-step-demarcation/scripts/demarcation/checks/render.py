"""第 2 步：渲染+反解后逐项检查 prompt 是否正确。

与源脚本的差异（K10）：消息内容比对先做空白归一化再取片段，避免模型模板
换行/空白重排导致“消息缺失”误判为 CHAT_TEMPLATE。
"""
import re


def _normalize(value):
    return re.sub(r'\s+', ' ', value).strip()


def _check_messages(text, req):
    normalized = _normalize(text)
    missing = []
    for message in req.get('messages', []):
        content = message.get('content') if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            fragment = _normalize(content)[:24]
            if fragment and fragment not in normalized:
                missing.append(str(message.get('role')))
    if missing:
        return 'FAIL', '缺失消息 role: %s' % ', '.join(missing)
    return 'PASS', ''


def _check_tools(text, req, tools_rendered):
    tools = req.get('tools') or []
    if not tools:
        return None
    names = [(tool.get('function') or {}).get('name') for tool in tools]
    missing = [name for name in names if name and name not in text]
    if not missing:
        return 'PASS', ''
    if tools_rendered is False:
        return 'WARN', '未检到 %s (本地渲染已降级为不带 tools, 老版 transformers)' % missing
    if tools_rendered is None:
        return 'WARN', '未检到 %s (/v1/tokenize 可能未透传 tools, 需人工确认)' % missing
    return 'WARN', '未检到 %s (模板可能不渲染 tools 或靠 parser 侧注入, 需人工确认)' % missing


def check_rendered(text, req, tools_rendered):
    checks = []
    if not text or not text.strip():
        checks.append({'name': 'prompt 非空', 'level': 'FAIL', 'detail': '渲染结果为空'})
        return checks
    checks.append({'name': 'prompt 非空', 'level': 'PASS', 'detail': ''})

    unresolved = re.findall(r'\{\{|\{%', text)
    checks.append({'name': '无未填充模板变量',
                   'level': 'FAIL' if unresolved else 'PASS',
                   'detail': ('残留 %d 处模板变量' % len(unresolved)) if unresolved else ''})

    level, detail = _check_messages(text, req)
    checks.append({'name': '各条消息内容已进入 prompt', 'level': level, 'detail': detail})

    tool = _check_tools(text, req, tools_rendered)
    if tool is not None:
        checks.append({'name': '工具描述进入 prompt', 'level': tool[0], 'detail': tool[1]})
    return checks
