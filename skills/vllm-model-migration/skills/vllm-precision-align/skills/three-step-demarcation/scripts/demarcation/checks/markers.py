"""原始输出的工具调用与 thinking 标记识别。

标记集合内置常见模板；检测不到不等于没有工具调用（模型可能使用非标准格式），
乱码/内容缺失判定为启发式，须配合人工查看 raw text。扩展方式：改本文件
TOOL_PATTERNS / THINK_OPEN_PATTERNS / THINK_CLOSE_PATTERNS 常量。
"""
import re

TOOL_PATTERNS = [
    r"<tool_call>",
    r"<\|tool_calls_section_begin\|>",
    r"<\|tool_call_begin\|>",
    r"<function=",
    r"<\|python_tag\|>",
    r'\[\s*\{\s*"name"\s*:',
    r'"function"\s*:\s*\{',
]
THINK_OPEN_PATTERNS = [r"<think\b[^>]*>", r"<reasoning\b[^>]*>"]
THINK_CLOSE_PATTERNS = [r"</think>", r"</reasoning>"]


def analyze_markers(text):
    """返回 has_toolcall_text / matched_tool_patterns / has_think_text /
    think_closed / content_after_think / only_thinking。"""
    matched = [pattern for pattern in TOOL_PATTERNS if re.search(pattern, text)]
    open_hit = any(re.search(pattern, text) for pattern in THINK_OPEN_PATTERNS)
    close = None
    for pattern in THINK_CLOSE_PATTERNS:
        close = re.search(pattern, text)
        if close:
            break
    think_closed = close is not None
    content_after = text[close.end():] if close else ''
    only_thinking = (open_hit and not matched
                     and (not think_closed or not content_after.strip()))
    return {'has_toolcall_text': bool(matched),
            'matched_tool_patterns': matched,
            'has_think_text': open_hit,
            'think_closed': think_closed,
            'content_after_think': content_after,
            'only_thinking': only_thinking}
