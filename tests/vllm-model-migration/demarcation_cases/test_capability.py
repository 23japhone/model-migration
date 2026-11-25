"""版本探测、契约解析与能力探测单元测试。"""
import sys
from pathlib import Path
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/skills/three-step-demarcation/scripts'
sys.path.insert(0, str(SCRIPTS))

from demarcation.probe import capability  # noqa: E402


class ParseVersionTests(unittest.TestCase):
    def test_plain_version(self):
        self.assertEqual(capability._parse_version('0.20.2'), (0, 20, 2))

    def test_rc_suffix(self):
        self.assertEqual(capability._parse_version('0.20.2rc1'), (0, 20, 2))

    def test_v_prefix(self):
        self.assertEqual(capability._parse_version('v0.20.2'), (0, 20, 2))

    def test_invalid(self):
        self.assertIsNone(capability._parse_version('unknown'))
        self.assertIsNone(capability._parse_version(''))


class ContractTests(unittest.TestCase):
    def test_old_render_is_list(self):
        contract = capability.resolve_contract((0, 16, 0))
        self.assertTrue(contract['render_supported'])
        self.assertTrue(contract['render_response_is_list'])
        self.assertEqual(contract['render_token_key'], 'prompt_token_ids')
        self.assertFalse(contract['render_requires_scale_out'])

    def test_new_render_is_object(self):
        contract = capability.resolve_contract((0, 20, 2))
        self.assertFalse(contract['render_response_is_list'])
        self.assertEqual(contract['render_token_key'], 'token_ids')
        self.assertFalse(contract['render_requires_scale_out'])

    def test_scale_out_required_from_030(self):
        contract = capability.resolve_contract((0, 30, 0))
        self.assertTrue(contract['render_requires_scale_out'])

    def test_unknown_version_is_liberal(self):
        contract = capability.resolve_contract(None)
        self.assertTrue(contract['render_supported'])
        self.assertFalse(contract['render_response_is_list'])
        self.assertEqual(contract['completions_token_ids_field'], 'return_token_ids')
        self.assertTrue(contract['tokenize_send_tools'])

    def test_old_completions_drops_token_ids_field(self):
        contract = capability.resolve_contract((0, 9, 0))
        self.assertIsNone(contract['completions_token_ids_field'])


class RootEndpointTests(unittest.TestCase):
    def test_with_v1_suffix(self):
        self.assertEqual(capability.root_endpoint('http://h:8000/v1'), 'http://h:8000')

    def test_without_v1_suffix(self):
        self.assertEqual(capability.root_endpoint('http://h:8000'), 'http://h:8000')


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, route, payload=None):
        self.requests.append((route, payload))
        return self._responses.pop(0)


class DetectVersionTests(unittest.TestCase):
    def test_detect(self):
        client = _FakeClient([(True, {'version': '0.20.2rc1'})])
        self.assertEqual(capability.detect_version(client), ('0.20.2rc1', (0, 20, 2)))

    def test_missing_returns_unknown(self):
        client = _FakeClient([(False, 'HTTP 404: not found')])
        self.assertEqual(capability.detect_version(client), (None, None))


class ProbeConventionTests(unittest.TestCase):
    def test_root_prefix_and_prompt_field(self):
        client = _FakeClient([
            (True, {'prompt': '你好'}),
            (True, {'prompt': '你好'}),
        ])
        result = capability.probe_convention(client, 'model')
        self.assertEqual(result, {'tokenize_prefix': '', 'detokenize_field': 'prompt'})

    def test_v1_prefix_and_text_field(self):
        client = _FakeClient([
            (False, 'HTTP 404: not found'),
            (True, {'text': '你好'}),
            (True, {'text': '你好'}),
        ])
        result = capability.probe_convention(client, 'model')
        self.assertEqual(result, {'tokenize_prefix': '/v1', 'detokenize_field': 'text'})


if __name__ == '__main__':
    unittest.main()
