"""共享分配的行为回归；数值和设备均为离线合成输入。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts')]

from verification.customer.capacity import select

POLICY = 'idle_first_shared'
SPEC = {'weight_bytes': 2000, 'kv_bytes_per_token': 4, 'context_tokens': 100,
        'runtime_bytes_per_device': 100, 'allowed_tp': [1, 2, 4], 'memory_fraction': .9}


def cards(flags, free=900, total=1000):
    return [{'id': n, 'free_bytes': free, 'total_bytes': total, 'occupied': flag}
            for n, flag in enumerate(flags)]


class SharedCapacityTests(unittest.TestCase):
    def choose(self, devices, spec=None, requested=-1):
        return select(spec or SPEC, devices, requested, policy=POLICY)

    def test_all_four_occupied_selects_tp4_when_tp2_does_not_fit(self):
        value = self.choose(cards([True] * 4))
        self.assertEqual(value['devices'], [0, 1, 2, 3])
        self.assertEqual(value['tensor_parallel_size'], 4)
        self.assertEqual(value['sharing_mode'], 'shared')
        self.assertEqual(value['memory_fraction'], .855)
        self.assertEqual([row['reserve_bytes'] for row in value['budgets']], [45] * 4)

    def test_idle_combination_beats_smaller_shared_tp(self):
        devices = cards([False] * 4 + [True])
        devices[4].update(total_bytes=10000, free_bytes=9000)
        self.assertEqual(self.choose(devices)['devices'], [0, 1, 2, 3])

    def test_mixed_combination_uses_fewest_occupied_then_smallest_tp(self):
        devices = cards([False, False, False, True, True])
        self.assertEqual(self.choose(devices)['devices'], [0, 1, 2, 3])
        self.assertEqual(self.choose(devices)['sharing_mode'], 'shared')

    def test_shared_reserve_is_extra_and_can_make_boundary_insufficient(self):
        spec = {**SPEC, 'weight_bytes': 650, 'allowed_tp': [1], 'kv_bytes_per_token': 1}
        devices = cards([True], free=850)
        with self.assertRaises(ValueError) as caught:
            self.choose(devices, spec)
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic['code'], 'capacity_insufficient')
        self.assertIn('不足', diagnostic['candidates'][0]['selection_reason'])
        row = diagnostic['candidates'][0]['budgets'][0]
        self.assertEqual(row['reserve_bytes'], 43)
        self.assertEqual(row['required_bytes'], 850)
        self.assertGreater(row['shortfall_bytes'], 0)

    def test_sum_of_free_memory_cannot_hide_weakest_shard(self):
        devices = cards([True] * 4, free=9000, total=10000)
        devices[3]['free_bytes'] = 200
        with self.assertRaises(ValueError) as caught:
            self.choose(devices, {**SPEC, 'allowed_tp': [4]})
        self.assertEqual(caught.exception.diagnostic['code'], 'capacity_insufficient')

    def test_effective_ratio_is_safe_for_heterogeneous_cards_and_input_unchanged(self):
        devices = cards([True] * 4)
        devices[1].update(total_bytes=1100, free_bytes=901)
        original = copy.deepcopy(devices)
        value = self.choose(devices)
        for row in value['budgets']:
            self.assertLessEqual(row['budget_bytes'], row['free_bytes'] - row['reserve_bytes'])
            self.assertLessEqual(value['memory_fraction'], SPEC['memory_fraction'])
            self.assertGreaterEqual(row['budget_bytes'], row['required_bytes'])
        self.assertEqual(original, devices)

    def test_tp_unavailable_is_not_reported_as_capacity_insufficient(self):
        with self.assertRaises(ValueError) as caught:
            self.choose(cards([True, True]), {**SPEC, 'allowed_tp': [4]})
        self.assertEqual(caught.exception.diagnostic['code'], 'tp_unavailable')

    def test_legacy_stays_exclusive_and_explicit_selection_never_substitutes(self):
        devices = cards([True] * 4)
        with self.assertRaises(ValueError):
            select(SPEC, devices, -1)
        with self.assertRaises(ValueError):
            self.choose(devices, requested=[8])
        self.assertEqual(self.choose(devices, requested=[3, 2, 1, 0])['devices'], [3, 2, 1, 0])

    def test_customer_compiler_enables_policy_and_rejects_memory_overrides(self):
        from customer_workflow.compile.deployment import allocation_request, command
        task = {'environment': {'available_npu_devices': [0, 1, 2, 3]}, 'model': {'model_name': 'model'}}
        devices = cards([True] * 4)
        request = allocation_request(task, {'devices': devices}, SPEC)
        self.assertEqual(request['policy'], POLICY)
        allocation = self.choose(devices)
        recipe = {'runtime_model_path': '/model', 'dtype': 'bfloat16', 'runtime': {'python': '/python'},
                  'max_num_seqs': 1}
        argv = command(task, {}, recipe, allocation, 8000, 100)
        self.assertEqual(float(argv[argv.index('--gpu-memory-utilization') + 1]), allocation['memory_fraction'])
        for key in ('kv_cache_memory_bytes', 'num_gpu_blocks_override', 'device_memory_utilization'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                command(task, {}, {**recipe, 'server_options': {key: 100}}, allocation, 8000, 100)

    def test_capacity_failure_is_user_decision_and_independent_work_continues(self):
        from customer_workflow import recovery, runner, effects
        from verification.customer import cli
        with self.assertRaises(ValueError) as caught:
            self.choose(cards([True] * 4, free=100))
        self.assertEqual(cli.blocked(caught.exception)['diagnostic']['code'], 'capacity_insufficient')
        error = effects.OperationBlocked(cli.blocked(caught.exception))
        session = Mock()
        with patch.object(effects, 'operation', side_effect=error):
            self.assertIsNone(runner.guarded(session, 'migration', lambda: recovery.allocate(session, {}, Path('/unused'))))
        decision = session.step.call_args.kwargs['decision']
        self.assertEqual(decision['owner'], 'user')
        self.assertIn('更换', decision['message'])
        self.assertEqual(runner.guarded(session, 'independent', lambda: 'prepared'), 'prepared')


if __name__ == '__main__':
    unittest.main()
