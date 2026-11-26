"""端到端验收：mock vLLM 服务 + 真实 three-step-demarcation 代码。"""
import sys
from pathlib import Path
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnosis import service  # noqa: E402
from mock_vllm import MockVLLM  # noqa: E402


class EndToEndTests(unittest.TestCase):
    def test_known_anomaly_yields_tool_parser(self):
        with MockVLLM() as vllm:
            with tempfile.TemporaryDirectory() as raw:
                root = str(Path(raw).resolve())  # Windows temp 目录可能是符号链接，先 resolve 成规范路径
                anomaly = {'sample_id': 1, 'anomaly_type': 'empty_tool_calls',
                           'request': {'messages': [{'role': 'user', 'content': '北京今天天气怎么样'}],
                                       'tools': [{'type': 'function', 'function': {'name': 'get_weather'}}],
                                       'tool_choice': 'auto'},
                           'service': {'endpoint': vllm.endpoint, 'model': 'Qwen3.5-27B'}}
                code, receipt = service.demarcate_anomaly(anomaly, root, 'run-1', timeout=60)
                self.assertEqual(receipt.get('status'), 'completed')
                primary = receipt.get('detailed_report', {}).get('primary')
                self.assertEqual(primary, 'TOOL_PARSER')


if __name__ == '__main__':
    unittest.main()
