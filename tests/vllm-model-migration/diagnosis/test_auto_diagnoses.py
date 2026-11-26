"""alignment 自动诊断编排的集成测试（mock 评测结果 + mock 定界）。"""
import sys
from pathlib import Path
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/scripts'
sys.path.insert(0, str(SCRIPTS))

from alignment import runner  # noqa: E402


class AutoDiagnosesTests(unittest.TestCase):
    def _manifest(self):
        return {
            'run_id': 'run-1',
            'tasks': {
                'evaluations': {'eval-1': {'status': 'completed'}},
                'diagnoses': {},
            },
            'results': {
                'eval-1': {'conclusions': {'evaluation_validity': 'pass'},
                           'anomalies': [
                               {'sample_id': 1, 'anomaly_type': 'empty_tool_calls',
                                'request': {'messages': [{'role': 'user', 'content': 'hi'}]},
                                'service': {'endpoint': 'http://127.0.0.1:8000', 'model': 'm'}},
                           ]},
            },
            'uncovered': [],
        }

    def test_auto_diagnose_collects_result(self):
        manifest = self._manifest()
        receipt = {'status': 'completed', 'conclusions': {'demarcation': 'pass'}, 'uncovered': []}
        with mock.patch.object(runner, 'demarcate_anomaly', return_value=(0, receipt)):
            with mock.patch.object(runner, 'save'):
                runner.auto_diagnoses({}, '/root', '/root/x', manifest, 120, 'run')
        diag_id = 'auto-eval-1-1-empty_tool_calls'
        self.assertEqual(manifest['results'][diag_id]['conclusions'], {'demarcation': 'pass'})

    def test_auto_diagnose_disabled(self):
        manifest = self._manifest()
        with mock.patch.object(runner, 'demarcate_anomaly') as fake:
            with mock.patch.object(runner, 'save'):
                runner.auto_diagnoses({'auto_diagnose': False}, '/root', '/root/x', manifest, 120, 'run')
        fake.assert_not_called()

    def test_skips_non_evaluation_results(self):
        manifest = self._manifest()
        manifest['results']['diagnoses'] = {'anomalies': [{'sample_id': 9, 'anomaly_type': 'x'}]}
        with mock.patch.object(runner, 'demarcate_anomaly',
                               return_value=(0, {'status': 'completed', 'conclusions': {}, 'uncovered': []})) as fake:
            with mock.patch.object(runner, 'save'):
                runner.auto_diagnoses({}, '/root', '/root/x', manifest, 120, 'run')
        # 只处理 evaluations 组的 results，diagnoses 组的 results 不触发定界
        self.assertEqual(fake.call_count, 1)


if __name__ == '__main__':
    unittest.main()
