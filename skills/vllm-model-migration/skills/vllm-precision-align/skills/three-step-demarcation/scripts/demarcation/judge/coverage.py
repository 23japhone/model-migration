"""证据齐全度：按 primary 结论核对必需证据，缺项写入 uncovered。

防止“证据不全但报告看起来一切正常”。结论可挂 task-result.schema.json 的
uncovered 数组，供消费者判断哪些环节仍需补证据。
"""


def _request_gap(step1, render, raw):
    items = step1.get('items', [])
    if any(item['level'] == 'FAIL' for item in items):
        return []
    return ['REQUEST 结论缺少第 1 步具体 FAIL 明细']


def _chat_template_gap(step1, render, raw):
    result = []
    if not any(check['level'] == 'FAIL' for check in render.get('checks', [])):
        result.append('CHAT_TEMPLATE 结论缺少第 2 步 FAIL 检查项')
    if not render.get('prompt_head'):
        result.append('CHAT_TEMPLATE 结论缺少 prompt_head 证据')
    return result


def _model_numeric_gap(step1, render, raw):
    garbage = raw.get('garbage', {})
    if garbage.get('is_garbage') and garbage.get('reasons'):
        return []
    return ['MODEL_NUMERIC 结论缺少乱码特征明细']


def _tool_parser_gap(step1, render, raw):
    if raw.get('matched_tool_patterns'):
        return []
    return ['TOOL_PARSER 结论缺少命中的工具调用标记']


def _thinking_parser_gap(step1, render, raw):
    if raw.get('has_think_text'):
        return []
    return ['THINKING_PARSER 结论缺少 think 标记证据']


def _service_output_gap(step1, render, raw):
    return ['SERVICE_OUTPUT 为启发式结论, 须人工对照 raw_completion.json 与 chat_response.json 复核']


def _model_incomplete_gap(step1, render, raw):
    if raw.get('finish_reason') is not None:
        return []
    return ['MODEL_INCOMPLETE 结论缺少 finish_reason 证据']


def _inconclusive_gap(step1, render, raw):
    return ['INCONCLUSIVE: 证据不完整, 补齐后再定界']


_GAP_HANDLERS = {
    'REQUEST': _request_gap,
    'CHAT_TEMPLATE': _chat_template_gap,
    'MODEL_NUMERIC': _model_numeric_gap,
    'TOOL_PARSER': _tool_parser_gap,
    'THINKING_PARSER': _thinking_parser_gap,
    'SERVICE_OUTPUT': _service_output_gap,
    'MODEL_INCOMPLETE': _model_incomplete_gap,
    'INCONCLUSIVE': _inconclusive_gap,
}


def _verdict_uncovered(primary, step1, render, raw):
    handler = _GAP_HANDLERS.get(primary)
    return handler(step1, render, raw) if handler else []


def _common_uncovered(step1, mode, evidence_files):
    result = []
    if not step1.get('fatal'):
        for name in ('rendered', 'rendered_prompt', 'raw_completion', 'chat_response'):
            if name not in evidence_files:
                result.append('缺少证据文件: %s' % name)
        if mode != 'render':
            result.append('渲染途径为 %s, token_ids 未经服务端 render 确认' % mode)
    return result


def coverage(primary, step1, render, raw, chat, mode, evidence_files):
    uncovered = _verdict_uncovered(primary, step1, render, raw)
    uncovered.extend(_common_uncovered(step1, mode, evidence_files))
    return uncovered
