"""目标 decode 的可选协议与两种独立的调度坐标。"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))
from verification.runtime.capture import contract, session


class DecodeCacheTests(unittest.TestCase):
    def test_deployment_opt_in_and_arm_selection(self):
        capture = {'adapter': contract.ADAPTER, 'output_root': '/work/capture',
                   'decode_cache': {'max_snapshot_bytes': 16000000}}
        contract.validate({'worker_capture': capture})
        arm = {'operation': 'arm', 'source': {'path': '/work/session', 'sha256': '0'*64},
               'trace_id': 'trace', 'sample_key': 'sample', 'native_id': 3, 'cache_output_index': 1}
        session.validate(arm)
        with self.assertRaises(ValueError):
            session.validate({**arm, 'cache_output_index': False})
        with self.assertRaises(ValueError):
            contract.validate({'worker_capture': {**capture, 'decode_cache': {'max_snapshot_bytes': -1}}})

    def test_full_plan_crosses_an_allocator_page(self):
        from verification.runtime.capture.adapters.decode_cache.plan import attention_plan
        plan = attention_plan([5, 2], 8, 4, 8)
        self.assertEqual(plan['expected_slot'], 16)
        self.assertEqual(plan['expected_read_blocks'], [10, 11, 4])

    def test_state_row_depends_on_cache_mode(self):
        from verification.runtime.capture.adapters.decode_cache.plan import state_row
        self.assertEqual(state_row([5, 2], 8, 8, 'align'), 2)
        self.assertEqual(state_row([5, 2], 8, 8, 'none'), 5)
        with self.assertRaises(ValueError):
            state_row([5], 8, 8, 'unknown')
