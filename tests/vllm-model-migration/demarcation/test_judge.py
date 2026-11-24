"""三步定界判定、标记、乱码与渲染检查单元测试。"""
import sys
from pathlib import Path
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/skills/three-step-demarcation/scripts'
sys.path.insert(0, str(SCRIPTS))

from demarcation.checks import garbage  # noqa: E402
from demarcation.checks import markers  # noqa: E402
from demarcation.checks import render as render_checks  # noqa: E402
from demarcation.checks import request as request_check  # noqa: E402
from demarcation.judge import decide as decide_module  # noqa: E402
from demarcation.judge import verdicts  # noqa: E402

THRESHOLDS = verdicts.load_profile()['thresholds']['garbage']


def _raw(text, **overrides):
    value = decide_module.analyze_raw(text, THRESHOLDS)
    value.update(overrides)
    return value


class DecideTests(unittest.TestCase):
    def test_fatal_request_is_request(self):
        verdicts_list, primary = decide_module.decide(True, False, True, {}, {}, False)
        self.assertEqual((verdicts_list, primary), (['REQUEST'], 'REQUEST'))

    def test_nonfatal_request_keeps_request(self):
        verdicts_list, primary = decide_module.decide(False, True, True, {}, {}, False)
        self.assertIn('REQUEST', verdicts_list)
        self.assertEqual(primary, 'REQUEST')

    def test_bad_render_is_chat_template(self):
        verdicts_list, primary = decide_module.decide(False, False, False, {}, {}, False)
        self.assertEqual(primary, 'CHAT_TEMPLATE')

    def test_empty_raw_is_inconclusive(self):
        raw = _raw('')
        verdicts_list, primary = decide_module.decide(False, False, True, raw, {}, False)
        self.assertEqual(primary, 'INCONCLUSIVE')

    def test_only_thinking_is_model_incomplete(self):
        raw = _raw('<think>x</think>')
        verdicts_list, primary = decide_module.decide(False, False, True, raw, {}, False)
        self.assertEqual(primary, 'MODEL_INCOMPLETE')

    def test_garbage_is_model_numeric(self):
        raw = _raw('你好世界' * 60)
        verdicts_list, primary = decide_module.decide(False, False, True, raw, {}, False)
        self.assertEqual(primary, 'MODEL_NUMERIC')

    def test_toolcall_text_without_tool_calls_is_tool_parser(self):
        raw = _raw('好的，我来调用工具。<tool_call>{"name": "get_weather"}</tool_call>')
        chat = {'content': '', 'n_tool_calls': 0, 'reasoning': '', 'finish_reason': 'stop'}
        verdicts_list, primary = decide_module.decide(False, False, True, raw, chat, True)
        self.assertEqual(primary, 'TOOL_PARSER')

    def test_closed_think_without_reasoning_is_thinking_parser(self):
        raw = _raw('<think>思考过程</think>最终回答')
        chat = {'content': '最终回答', 'n_tool_calls': 0, 'reasoning': '', 'finish_reason': 'stop'}
        verdicts_list, primary = decide_module.decide(False, False, True, raw, chat, False)
        self.assertEqual(primary, 'THINKING_PARSER')

    def test_plain_answer_but_chat_empty_is_service_output(self):
        raw = _raw('北京今天天气晴朗，适合出行。')
        chat = {'content': '', 'n_tool_calls': 0, 'reasoning': '', 'finish_reason': 'stop'}
        verdicts_list, primary = decide_module.decide(False, False, True, raw, chat, False)
        self.assertEqual(primary, 'SERVICE_OUTPUT')

    def test_tools_without_toolcall_is_model_output(self):
        raw = _raw('北京今天天气晴朗，适合出行。')
        chat = {'content': '北京今天天气晴朗', 'n_tool_calls': 0, 'reasoning': '', 'finish_reason': 'stop'}
        verdicts_list, primary = decide_module.decide(False, False, True, raw, chat, True)
        self.assertEqual(primary, 'MODEL_OUTPUT')

    def test_normal_chat_is_pass(self):
        raw = _raw('北京今天天气晴朗，适合出行。')
        chat = {'content': '北京今天天气晴朗，适合出行。', 'n_tool_calls': 0, 'reasoning': '', 'finish_reason': 'stop'}
        verdicts_list, primary = decide_module.decide(False, False, True, raw, chat, False)
        self.assertEqual(primary, 'PASS')


class MarkerTests(unittest.TestCase):
    def test_toolcall_and_think_detection(self):
        text = '<tool_call>{"name":"f"}</tool_call>'
        result = markers.analyze_markers(text)
        self.assertTrue(result['has_toolcall_text'])
        self.assertFalse(result['has_think_text'])

    def test_think_close_and_content_after(self):
        text = '<think>思考</think>最终回答'
        result = markers.analyze_markers(text)
        self.assertTrue(result['has_think_text'])
        self.assertTrue(result['think_closed'])
        self.assertEqual(result['content_after_think'], '最终回答')
        self.assertFalse(result['only_thinking'])


class GarbageTests(unittest.TestCase):
    def test_whitespace_only_is_garbage(self):
        result = garbage.analyze_garbage('   \n\t  ', THRESHOLDS)
        self.assertTrue(result['is_garbage'])

    def test_short_text_is_skipped(self):
        result = garbage.analyze_garbage('短文本', THRESHOLDS)
        self.assertFalse(result['checked'])
        self.assertFalse(result['is_garbage'])

    def test_repetition_is_garbage(self):
        text = '你好世界' * 50
        result = garbage.analyze_garbage(text, THRESHOLDS)
        self.assertTrue(result['is_garbage'])


class RenderCheckTests(unittest.TestCase):
    def test_message_with_newline_still_matches_after_normalization(self):
        req = {'messages': [{'role': 'user', 'content': '北京今天\n天气怎么样'}]}
        text = 'system: 你是一个助手\nuser: 北京今天 天气怎么样\nassistant: '
        checks = render_checks.check_rendered(text, req, None)
        message_check = [c for c in checks if c['name'] == '各条消息内容已进入 prompt'][0]
        self.assertEqual(message_check['level'], 'PASS')

    def test_missing_message_is_fail(self):
        req = {'messages': [{'role': 'user', 'content': '完全不同的内容'}]}
        text = 'system: 你是一个助手\nuser: 其他内容\n'
        checks = render_checks.check_rendered(text, req, None)
        message_check = [c for c in checks if c['name'] == '各条消息内容已进入 prompt'][0]
        self.assertEqual(message_check['level'], 'FAIL')


class RequestCheckTests(unittest.TestCase):
    def test_valid_request_passes(self):
        req = {'messages': [{'role': 'user', 'content': '你好'}]}
        items, fatal = request_check.step1_check_request(req)
        self.assertFalse(fatal)
        self.assertFalse(request_check.step1_has_fail(items))

    def test_empty_messages_is_fatal(self):
        items, fatal = request_check.step1_check_request({'messages': []})
        self.assertTrue(fatal)

    def test_bad_tool_structure_is_fail(self):
        req = {'messages': [{'role': 'user', 'content': '你好'}],
               'tools': [{'type': 'function'}]}
        items, fatal = request_check.step1_check_request(req)
        self.assertFalse(fatal)
        self.assertTrue(request_check.step1_has_fail(items))


if __name__ == '__main__':
    unittest.main()
