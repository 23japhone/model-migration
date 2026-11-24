"""第 3 步原始输出解析与九类定界判定；decide() 保留源脚本口径。"""
from .verdicts import PRIORITY
from ..checks.garbage import analyze_garbage
from ..checks.markers import analyze_markers


def analyze_raw(text, thresholds):
    markers = analyze_markers(text)
    result = {'text': text}
    result.update(markers)
    result['garbage'] = analyze_garbage(text, thresholds)
    return result


def analyze_chat(data):
    try:
        choice = data['choices'][0]
    except Exception:
        return {'error': '响应无 choices'}
    message = choice.get('message') or {}
    reasoning = message.get('reasoning_content')
    if reasoning is None:
        reasoning = message.get('reasoning') or ''
    return {'content': message.get('content') or '',
            'n_tool_calls': len(message.get('tool_calls') or []),
            'reasoning': reasoning or '',
            'finish_reason': choice.get('finish_reason')}


def _numeric_verdict(raw):
    if raw.get('garbage', {}).get('is_garbage'):
        return 'MODEL_NUMERIC'
    return None


def _tool_parser_verdict(raw, chat):
    if raw.get('has_toolcall_text') and chat.get('n_tool_calls', 0) == 0:
        return 'TOOL_PARSER'
    return None


def _thinking_parser_verdict(raw, chat):
    if raw.get('has_think_text') and raw.get('think_closed') and not chat.get('reasoning'):
        return 'THINKING_PARSER'
    return None


def _service_output_verdict(raw, chat):
    if raw.get('has_toolcall_text') or raw.get('garbage', {}).get('is_garbage'):
        return None
    raw_answer = raw.get('content_after_think') if raw.get('think_closed') else raw.get('text', '')
    raw_vis = ''.join(ch for ch in (raw_answer or '') if not ch.isspace())
    chat_vis = ''.join(ch for ch in (chat.get('content') or '') if not ch.isspace())
    if len(raw_vis) >= 10 and not chat_vis and chat.get('n_tool_calls', 0) == 0:
        return 'SERVICE_OUTPUT'
    return None


def _step3_verdicts(raw, chat):
    verdicts = []
    for verdict in (_numeric_verdict(raw), _tool_parser_verdict(raw, chat),
                    _thinking_parser_verdict(raw, chat), _service_output_verdict(raw, chat)):
        if verdict:
            verdicts.append(verdict)
    return verdicts


def decide(step1_fatal, step1_fail, render_ok, raw, chat, has_tools):
    """按 decision-tree.md 判定；返回 (verdicts, primary)。"""
    if step1_fatal:
        return ['REQUEST'], 'REQUEST'
    verdicts = ['REQUEST'] if step1_fail else []
    if not render_ok:
        verdicts.append('CHAT_TEMPLATE')
        verdicts.sort(key=PRIORITY.index)
        return verdicts, verdicts[0]
    if not (raw.get('text') or '').strip():
        verdicts.append('INCONCLUSIVE')  # completions 成功但拿不到原始文本
        verdicts.sort(key=PRIORITY.index)
        return verdicts, verdicts[0]
    if raw.get('only_thinking'):
        verdicts.append('MODEL_INCOMPLETE')
    else:
        verdicts.extend(_step3_verdicts(raw, chat))
    if not verdicts:
        verdicts.append('MODEL_OUTPUT' if (has_tools and not raw.get('has_toolcall_text'))
                        else 'PASS')
    verdicts.sort(key=PRIORITY.index)
    return verdicts, verdicts[0]
