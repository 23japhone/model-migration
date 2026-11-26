"""客户三遍配置和续接范围。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/scripts'
sys.path.insert(0, str(SCRIPTS))
from customer_workflow.contract import normalize


class TensorCustomer(unittest.TestCase):
    def task(self, **fields):
        return normalize({'model': {}, 'environment': {}, 'accuracy': fields})

    def test_tolerances(self):
        self.assertNotIn('tensor_alignment', self.task()['accuracy'])
        for policy in ({}, {'atol': 0}, {'rtol': 0}, {'atol': 0.5, 'rtol': 0}):
            self.assertEqual(self.task(tensor_alignment=policy)['accuracy']['tensor_alignment'], policy)
        for policy in ({'foo': 0}, {'atol': True, 'rtol': 0},
                       {'atol': float('inf'), 'rtol': 0}, {'atol': 1, 'rtol': -1}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.task(tensor_alignment=policy)

    def test_only_existing_tensor_thresholds_can_change(self):
        from customer_workflow.tensor_alignment import tolerance_change
        old, new = self.task(tensor_alignment={}), self.task(tensor_alignment={'atol': 1, 'rtol': 0})
        self.assertTrue(tolerance_change(old, new))
        self.assertTrue(tolerance_change(self.task(), new))
        changed = deepcopy(new)
        changed['environment']['container_name'] = 'another'
        self.assertFalse(tolerance_change(old, changed))
