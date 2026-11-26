"""diagnosis/service 服务定界的单元测试。"""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/scripts'
sys.path.insert(0, str(SCRIPTS))

from diagnosis import service  # noqa: E402


class DemarcateAnomalyTests(unittest.TestCase):
    def test_writes_badcase_and_calls_demarcate(self):
        anomaly = {'sample_id': 42, 'anomaly_type': 'empty_tool_calls',
                   'request': {'messages': [{'role': 'user', 'content': 'hi'}]},
                   'service': {'endpoint': 'http://127.0.0.1:8000', 'model': 'm'}}
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(service, 'demarcate', return_value=(0, {'status': 'completed'})) as fake:
                code, receipt = service.demarcate_anomaly(anomaly, root, 'run-1')
            self.assertEqual(code, 0)
            self.assertEqual(receipt, {'status': 'completed'})
            args = fake.call_args.args
            self.assertEqual(args[0], anomaly['service'])
            self.assertEqual(set(args[1]), {'path', 'sha256'})
            self.assertTrue(Path(args[1]['path']).is_file())
            self.assertEqual(args[3], 'run-1')


if __name__ == '__main__':
    unittest.main()
