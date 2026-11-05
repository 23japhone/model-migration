"""模拟 CANN/宿主观察；不占用真实 NPU。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))


class MappingTests(unittest.TestCase):
    def test_zero_based_native_mapping_is_also_observed(self):
        from verification.devices.policy import resolve
        env, selected = resolve([0, 1], [{'physical_id': 0, 'logical_id': 0},
                                        {'physical_id': 1, 'logical_id': 1}], {})
        self.assertEqual(env['ASCEND_RT_VISIBLE_DEVICES'], '0,1')
        self.assertEqual([row['physical_id'] for row in selected], [0, 1])

    def test_nonzero_physical_devices_resolve_to_observed_logical_ids(self):
        from verification.devices.policy import resolve
        rows = [{'physical_id': 4, 'logical_id': 1}, {'physical_id': 7, 'logical_id': 0}]
        env, selected = resolve([4, 7], rows, {})
        self.assertEqual(env['ASCEND_RT_VISIBLE_DEVICES'], '1,0')
        self.assertEqual(selected, [dict(rows[0], visible_index=0), dict(rows[1], visible_index=1)])

    def test_explicit_logical_order_is_preserved_and_wrong_ids_block(self):
        from verification.devices.policy import resolve
        rows = [{'physical_id': 4, 'logical_id': 0}, {'physical_id': 5, 'logical_id': 1}]
        env, selected = resolve([4, 5], rows, {'ASCEND_RT_VISIBLE_DEVICES': '1,0'})
        self.assertEqual([row['physical_id'] for row in selected], [5, 4])
        for value in ('4,5', '0', '0,0', '0,-1', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve([4, 5], rows, {'ASCEND_RT_VISIBLE_DEVICES': value})

    def test_same_count_wrong_physical_or_duplicate_mapping_blocks(self):
        from verification.devices.policy import resolve, validate_visible
        rows = [{'physical_id': 4, 'logical_id': 0}, {'physical_id': 5, 'logical_id': 1}]
        _, selected = resolve([4, 5], rows, {})
        wrong = copy.deepcopy(selected); wrong[0]['physical_id'] = 0
        with self.assertRaises(ValueError):validate_visible(selected, wrong)
        with self.assertRaises(ValueError):resolve([4, 5], [rows[0], rows[0]], {})

    def test_borrowed_absent_filter_is_observed_not_generated(self):
        from verification.devices.policy import resolve
        rows = [{'physical_id': 4, 'logical_id': 0}, {'physical_id': 5, 'logical_id': 1}]
        env, _ = resolve([4, 5], rows, {}, ownership='borrowed')
        self.assertNotIn('ASCEND_RT_VISIBLE_DEVICES', env)


class WorkerTests(unittest.TestCase):
    def test_runtime_inspection_does_not_prefilter_unselected_cards(self):
        from verification.runtime import inspection
        rows = [{'device': d, 'chip_id': 0, 'pid': 100 + d} for d in (0, 4, 5)]
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(inspection, 'listener_owners', return_value=[100]), \
             patch.object(inspection.inventory, 'snapshot', return_value=rows), \
             patch.object(inspection, 'same_owner', return_value=True), self.assertRaisesRegex(ValueError, '范围外'):
            inspection.running_evidence({'npu': {'devices': [4, 5]},
                'service': {'endpoint': 'http://127.0.0.1:8000'}}, {}, Path(folder))

    def test_final_worker_confirmation_rejects_pid_reuse(self):
        from verification.runtime import inspection
        expected = {'pid': 100, 'pgid': 100, 'start_ticks': 'old'}
        with patch.object(inspection, 'process_identity', return_value={**expected, 'start_ticks': 'new'}), \
             self.assertRaisesRegex(ValueError, '身份'):
            inspection.confirm_workers({'listener_identities': [expected], 'npu_processes': []}, {})

    def test_same_service_on_unselected_card_is_rejected(self):
        from verification.devices.workers import select_workers
        rows = [{'device': d, 'chip_id': 0, 'pid': 100 + d} for d in (0, 4, 5)]
        with self.assertRaisesRegex(ValueError, '范围外'):
            select_workers(rows, [4, 5], lambda pid: True)

    def test_foreign_worker_on_selected_card_is_rejected(self):
        from verification.devices.workers import select_workers
        rows = [{'device': d, 'chip_id': 0, 'pid': 100 + d} for d in (4, 5)]
        with self.assertRaisesRegex(ValueError, '其他任务'):
            select_workers(rows, [4, 5], lambda pid: pid == 104)

    def test_foreign_worker_on_unselected_card_is_allowed(self):
        from verification.devices.workers import select_workers
        rows = [{'device': d, 'chip_id': 0, 'pid': 100 + d} for d in (0, 4, 5)]
        self.assertEqual(select_workers(rows, [4, 5], lambda pid: pid != 100), rows[1:])


class InjectionTests(unittest.TestCase):
    def test_docker_probe_and_launch_receive_effective_environment(self):
        from verification.runtime import execution, docker
        request = {'runtime': {'kind': 'docker', 'python': '/python',
                              'env': {'ASCEND_RT_VISIBLE_DEVICES': '1,0'}},
                   'service': {'command': ['/python', '-m', 'vllm']}}
        resource = {'identity': {'container_id': 'a' * 64}, 'nonce': 'nonce'}
        argv = execution.prefix(request, resource)
        self.assertIn('ASCEND_RT_VISIBLE_DEVICES=1,0', argv)
        with tempfile.TemporaryDirectory() as folder, patch.object(docker, 'run') as run:
            docker.launch(request, resource, Path(folder))
            self.assertIn('ASCEND_RT_VISIBLE_DEVICES=1,0', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
