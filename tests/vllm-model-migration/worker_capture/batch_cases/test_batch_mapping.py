"""同一物理前向的请求行必须跟随 worker 实际排列。"""
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest

import numpy as np

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))


class MappingTests(unittest.TestCase):
    def test_three_members_reordered_duplicate_token_and_padding(self):
        from verification.runtime.capture.adapters.batch.worker import input_rows
        plan = {name: {'trace_id': name.lower(), 'count': count, 'position': pos}
                for name, count, pos in [('B', 2, 5), ('C', 1, 0), ('A', 1, 5)]}
        runner = NS(input_batch=NS(req_ids=['A', 'B', 'C']),
                    query_start_loc=NS(np=np.array([0, 1, 3, 4, 100])),
                    logits_indices=np.array([0, 2, 3]))
        rows = input_rows(plan, runner, np.array([71, 71, 9, 8, 99]), np.array([5, 5, 6, 0, 88]))
        self.assertEqual([r['input_ids'] for r in rows], [[71], [71, 9], [8]])
        self.assertEqual([r['token_range'] for r in rows], [[0, 1], [1, 3], [3, 4]])
        self.assertEqual([r['internal_id'] for r in rows], ['A', 'B', 'C'])
        runner.query_start_loc.np[2] = 4
        with self.assertRaises(ValueError):
            input_rows(plan, runner, np.array([71, 71, 9, 8]), np.array([5, 5, 6, 0]))
