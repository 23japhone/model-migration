"""评测侧异常判定：结构、判定、提取的单元测试。"""
import sys
from pathlib import Path
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/scripts'
sys.path.insert(0, str(SCRIPTS))

from precision import anomalies, anomaly_detect, anomaly_extract  # noqa: E402


class AnomalyStructureTests(unittest.TestCase):
    def test_valid_sample(self):
        value = anomalies.sample(42, 'empty_tool_calls', {'messages': []},
                                 {'endpoint': 'http://127.0.0.1:8000', 'model': 'm'})
        self.assertEqual(value['sample_id'], 42)
        self.assertEqual(value['anomaly_type'], 'empty_tool_calls')

    def test_negative_sample_id_rejected(self):
        with self.assertRaises(ValueError):
            anomalies.sample(-1, 'x', {'messages': []}, {'endpoint': 'http://a', 'model': 'm'})

    def test_empty_type_rejected(self):
        with self.assertRaises(ValueError):
            anomalies.sample(1, '', {'messages': []}, {'endpoint': 'http://a', 'model': 'm'})

    def test_bad_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            anomalies.sample(1, 'x', {'messages': []}, {'endpoint': 'ftp://a', 'model': 'm'})


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.profiles = {'schema_version': '1.0', 'anomalies': [
            {'type': 'empty_tool_calls', 'enabled': True, 'detect': {}, 'maps_to': []},
            {'type': 'garbage_output', 'enabled': True,
             'detect': {'thresholds': {'char_diversity': 0.3, 'dup_ngram_ratio': 0.5}}, 'maps_to': []},
            {'type': 'low_score', 'enabled': False, 'detect': {}, 'maps_to': []},
        ]}

    def test_empty_tool_calls(self):
        request = {'tools': [{'type': 'function'}]}
        response = {'content': '好的', 'tool_calls': None, 'reasoning_content': None}
        self.assertEqual(anomaly_detect.detect(self.profiles, request, response), ['empty_tool_calls'])

    def test_garbage(self):
        request = {'messages': []}
        response = {'content': '你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好你好',
                    'tool_calls': None, 'reasoning_content': None}
        self.assertEqual(anomaly_detect.detect(self.profiles, request, response), ['garbage_output'])

    def test_normal_not_flagged(self):
        request = {'messages': []}
        response = {'content': '今天天气晴朗，温度适宜。', 'tool_calls': None, 'reasoning_content': None}
        self.assertEqual(anomaly_detect.detect(self.profiles, request, response), [])

    def test_disabled_skipped(self):
        self.assertNotIn('low_score', anomaly_detect.detect(self.profiles, {}, {'content': 'x'}))


class CollectTests(unittest.TestCase):
    def test_collect(self):
        profiles = {'schema_version': '1.0', 'anomalies': [
            {'type': 'empty_tool_calls', 'enabled': True, 'detect': {}, 'maps_to': []},
        ]}
        service = {'endpoint': 'http://127.0.0.1:8000', 'model': 'm'}
        samples = [(1, {'tools': [{}]}, {'content': 'x', 'tool_calls': None, 'reasoning_content': None}),
                   (2, {}, {'content': '正常', 'tool_calls': None, 'reasoning_content': None})]
        result = anomaly_detect.collect_anomalies(profiles, samples, service)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['sample_id'], 1)
        self.assertEqual(result[0]['service'], service)


class ResponseViewTests(unittest.TestCase):
    def test_chat(self):
        body = {'choices': [{'message': {'content': 'c', 'tool_calls': [{'name': 'f'}], 'reasoning_content': 'r'}}]}
        self.assertEqual(anomaly_extract.response_view(body),
                         {'content': 'c', 'tool_calls': [{'name': 'f'}], 'reasoning_content': 'r'})

    def test_chat_missing_fields(self):
        body = {'choices': [{'message': {'content': 'c'}}]}
        self.assertEqual(anomaly_extract.response_view(body),
                         {'content': 'c', 'tool_calls': None, 'reasoning_content': None})

    def test_completions(self):
        body = {'choices': [{'text': 't'}]}
        self.assertEqual(anomaly_extract.response_view(body),
                         {'content': 't', 'tool_calls': None, 'reasoning_content': None})


if __name__ == '__main__':
    unittest.main()
