"""从已封存预测回查第二个输出，拒绝错配与不完整向量。"""
import unittest
from pathlib import Path
import sys
VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))
from verification.runtime.capture.index import correlate


class PredictionTests(unittest.TestCase):
    def check(self, positions=(3,), ids=(71,), candidate=92):
        forward = {'positions': list(positions), 'input_ids': list(ids), 'logits_indices': [0],
                   'internal_id': 'cmpl-test-0-random', 'forward_id': 'decode', 'output_index': 1}
        return correlate([11, 22, 33], [71, 92], 1, forward, candidate, 92)

    def test_second_token(self):
        self.assertEqual(self.check(), [11, 22, 33, 71])

    def test_wrong_position_or_input(self):
        for args in [((2,), (71,)), ((3,), (92,)), ((3,), (71,), 71)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.check(*args)
