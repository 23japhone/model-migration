from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.parallel_cases.test_identity import VERIFY
from verification.runtime.capture.adapters.parallel import EXECUTOR_SHA
from verification.runtime.capture.generation import vision
from verification.runtime.capture.parallel import receipt, topology


def group(size):
    ranks = list(range(size))
    devices = [{'physical_id': rank * 3 + 2, 'visible_index': rank} for rank in ranks]
    records = {f'worker-rank{rank}': {
        **devices[rank], 'rank': rank, 'tp_rank': rank, 'world_size': size, 'group_ranks': ranks,
        'namespace': 'ns', 'pid': rank + 12, 'layers': ['layer.0'], 'architecture': {},
        'decode_cache': {'max_snapshot_bytes': 100},
    } for rank in ranks}
    records['executor'] = {'world_size': size, 'output_rank': 0, 'sha256': EXECUTOR_SHA}
    return {'world_size': size, 'devices': devices}, records


class MembersTests(unittest.TestCase):
    def test_dynamic_size_and_device_membership(self):
        for size in (2, 3, 4, 8):
            with self.subTest(size=size):
                topo, records = group(size)
                ready = topology.validate_members(topo, records)
                self.assertEqual([w['physical_id'] for w in ready['rank_workers']],
                                 [device['physical_id'] for device in topo['devices']])
                wrong = {**topo, 'world_size': size + 1}
                with self.assertRaises(ValueError):
                    topology.validate_members(wrong, records)
                records[f'worker-rank{size - 1}']['physical_id'] = topo['devices'][0]['physical_id']
                with self.assertRaisesRegex(ValueError, '映射'):
                    topology.validate_members(topo, records)

    def test_missing_extra_and_duplicate_workers(self):
        topo, records = group(4)
        with self.assertRaisesRegex(ValueError, 'worker-rank3'):
            topology.validate_members(topo, {k: v for k, v in records.items() if k != 'worker-rank3'})
        with self.assertRaisesRegex(ValueError, 'worker-rank4'):
            topology.validate_members(topo, {**records, 'worker-rank4': records['worker-rank3']})
        records['worker-rank2']['pid'] = records['worker-rank3']['pid']
        with self.assertRaisesRegex(ValueError, '身份重复'):
            topology.validate_members(topo, records)

    def test_damaged_arm_binding_is_rank_local(self):
        topo, records = group(4)
        ready = topology.validate_members(topo, records)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            refs = topology.bind_at_arm(folder, ready)
            (folder / 'member-rank1.json').unlink()
            (folder / 'member-rank2.json').write_text('{}')
            records['worker-rank1']['pid'] += 200
            bound = topology.seal_members(folder, refs, lambda: ready, size=4)
            self.assertEqual(bound['rank_workers'][3], records['worker-rank3'])
            self.assertEqual(bound['rank_workers'][0], records['worker-rank0'])
            self.assertNotIn('pid', bound['rank_workers'][1])
            self.assertIn('rank1', bound['rank_workers'][1]['binding_error'])
            self.assertIn('rank2', bound['rank_workers'][2]['binding_error'])
            with self.assertRaisesRegex(ValueError, '成员数'):
                topology.seal_members(folder, refs[:3], lambda: ready, size=4)
            with self.assertRaisesRegex(ValueError, '其他请求'):
                topology.seal_members(folder, [refs[1], refs[0], *refs[2:]], lambda: ready, size=4)

    def test_request_budget_is_shared_across_every_member(self):
        topo, records = group(8)
        ready = {'rank_workers': [records[f'worker-rank{i}'] for i in range(8)]}
        totals = [{'snapshot_bytes': 2 + (i < 3)} for i in range(8)]
        intent = {'cache_capture': {'max_request_snapshot_bytes': 19}}
        errors = []
        receipt.check_budgets(intent, ready, [], totals, errors)
        self.assertFalse(errors)
        totals[6]['snapshot_bytes'] += 1
        receipt.check_budgets(intent, ready, [], totals, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn('rank6', errors[0])

    def test_image_identity_and_axes_cover_last_member(self):
        ranks = list(range(4))
        members = [{'rank': r, 'position_axes': [[0], [0], [int(r == 3)]],
                    'status': 'complete', 'errors': []} for r in ranks]
        receipt.compare_position_axes(members)
        self.assertTrue(all(m['status'] == 'incomplete' for m in members))
        def observed(image, timeline, rank, errors):
            return {'rank': rank, 'feature': {'offset': int(rank == 3)}}
        errors = []
        with patch.object(vision, 'rank_image', side_effect=observed):
            result = vision.bind_images([{}], [{'ranks': members}], errors, len(ranks))
        self.assertEqual([r['rank'] for r in result[0]['ranks']], ranks)
        self.assertTrue(any('rank3' in e for e in errors))
