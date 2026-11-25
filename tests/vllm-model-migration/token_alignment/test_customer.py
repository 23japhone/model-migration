"""客户输入只接受两项绝对容差，首轮成绩继续保持独立。"""
from pathlib import Path
import math
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from customer_workflow.contract import normalize


class TokenCustomer(unittest.TestCase):
    def normalize(self, policy):
        return normalize({'model': {}, 'environment': {}, 'accuracy': {'token_alignment': policy}})['accuracy']

    def test_two_absolute_limits(self):
        policy = {'raw_logits': {'atol': 0}, 'processed_scores': {'atol': .001}}
        self.assertEqual(self.normalize(policy)['token_alignment'], policy)

    def test_unchanged_legacy(self):
        self.assertNotIn('token_alignment', self.normalize(None))

    def test_invalid_limits(self):
        for invalid in ({'rtol': 0}, {'atol': True}, {'atol': -0.01}, {'atol': math.inf}, {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.normalize({'raw_logits': invalid, 'processed_scores': {'atol': 0}})

    def test_owned_single_card_service_gets_private_inactive_capture_mount(self):
        from customer_workflow.token_alignment.deployment import attach
        from types import SimpleNamespace
        from tempfile import TemporaryDirectory
        policy = {'raw_logits': {'atol': 0}, 'processed_scores': {'atol': 0}}
        with TemporaryDirectory() as temporary:
            dest = Path(temporary).resolve() / 'token-worker-capture'
            task = {'accuracy': {'token_alignment': policy}}
            verify = {'runtime': {'versions': {'vllm': '0.20.2+empty'}},
                      'service': {'ownership': 'owned', 'command': ['vllm', 'serve'], 'docker': {'mounts': []}},
                      'npu': {'devices': [0]}}
            session = SimpleNamespace(parent=dest.parent, value={'steps': {'allocation': {'receipt': {}}}})
            attach(session, task, verify)
            self.assertIn('--no-async-scheduling', verify['service']['command'])
            self.assertTrue(dest.is_dir())
            self.assertEqual(verify['worker_capture']['output_root'], str(dest))
            self.assertEqual(verify['service']['docker']['mounts'], [
                {'source': str(dest), 'target': str(dest), 'read_only': False}])
            legacy = {'service': {'ownership': 'owned'}, 'npu': {'devices': [0]}}
            attach(session, {'accuracy': {}}, legacy)
            self.assertNotIn('worker_capture', legacy)

    def test_only_matching_packages_are_used_among_mixed_context_evidence(self):
        from customer_workflow.token_alignment.stage import bundles
        from coordination.runtime.sources import ref
        import json
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            other = root / 'release-notes.txt'
            other.write_text('软件版本记录')
            found = root / 'gpu.json'
            found.write_text(json.dumps({'format': 'token-prediction-bundle-v1', 'quality_report_sha256': 'current'}))
            self.assertEqual(bundles({'evidence': [ref(other), ref(found)]}, {'sha256': 'current'}), [ref(found)])
