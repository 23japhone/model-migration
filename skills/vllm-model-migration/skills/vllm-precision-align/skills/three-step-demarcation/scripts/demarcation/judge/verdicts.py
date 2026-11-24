"""九类定界结论、优先级与结论档案。"""
import json

from .. import SKILL_ROOT

# 定界结论（可叠加，primary 取优先级最高者）。顺序即判定优先级：
# 前两步的“更上游”问题优先于第三步的解析/生成侧问题。
PRIORITY = ['REQUEST', 'CHAT_TEMPLATE', 'MODEL_NUMERIC', 'MODEL_INCOMPLETE',
            'TOOL_PARSER', 'THINKING_PARSER', 'SERVICE_OUTPUT', 'MODEL_OUTPUT',
            'INCONCLUSIVE', 'PASS']

VERDICTS = tuple(PRIORITY)


def load_profile():
    """读取 references/profiles.json 的建议话术与乱码阈值；失败即报错。"""
    path = SKILL_ROOT / 'references' / 'profiles.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version') != '1.0':
        raise ValueError('profiles.json 版本无效')
    return value
