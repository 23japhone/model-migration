"""Exercise real worker selection and evidence paths with offline TP process groups."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

VERIFY = next(Path(__file__).resolve().parents[4].glob('skills/*/skills/*/skills/verify-agent'))
sys.path.insert(0, str(VERIFY / 'scripts'))
from verification.runtime.capture.adapters import parallel, state
from verification.runtime.capture.adapters.worker import Recorder


class WorkerGroupFixture:
    def __init__(self, size):
        self.size = size
        self.rank = size - 1
        self.group = NS(world_size=size, rank_in_group=self.rank, ranks=list(range(size)), device_group=object())
        self.dist = Mock()
        self.dist.is_initialized.return_value = True
        self.dist.get_rank.return_value = self.rank
        self.dist.get_world_size.return_value = size
        self.dist.get_backend.return_value = 'hccl'
        self.npu = Mock()
        self.npu.current_device.return_value = 0
        self.modules = {'torch': NS(npu=self.npu, distributed=self.dist), 'torch.distributed': self.dist,
                        'vllm.distributed.parallel_state': NS(get_tp_group=lambda: self.group)}
        config = NS(world_size=size, tensor_parallel_size=size, pipeline_parallel_size=1)
        self.runner = NS(vllm_config=NS(parallel_config=config))

    def observe(self, root):
        physical = [3, 4, 7, 1, 5, 0, 2, 6][:self.size]
        devices = [{'physical_id': device, 'visible_index': index} for index, device in enumerate(physical)]
        state.write(root / 'parallel.json', {'devices': devices, 'world_size': self.size})
        with patch.dict(sys.modules, self.modules), patch.dict(os.environ, VERIFY_EAGER_ROOT=str(root)):
            return parallel.worker_group(self.runner)


class WorkerSizeTests(unittest.TestCase):
    def test_actual_group_and_mapped_physical_device(self):
        for size in (2, 3, 4, 8):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as base:
                fixture = WorkerGroupFixture(size)
                member = fixture.observe(Path(base))
                self.assertEqual((member['rank'], member['tp_rank'], member['world_size']), (size - 1, size - 1, size))
                self.assertEqual(member['group_ranks'], list(range(size)))
                self.assertEqual((member['physical_id'], member['visible_index']), (3, 0))

    def test_distributed_group_must_match_prepared_world(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            fixture = WorkerGroupFixture(4)
            fixture.group.ranks = [0, 1, 2, 5]
            with self.assertRaises(ValueError):
                fixture.observe(root)
            fixture.group.ranks = list(range(4))
            fixture.dist.get_world_size.return_value = 8
            with self.assertRaises(ValueError):
                fixture.observe(root)

    def test_schedule_probe_and_budget_follow_each_worker(self):
        identity = {'forward_id': 'f' * 32, 'internal_id': 'req', 'count': 1}
        schedule = NS(_verify_forward=identity, num_scheduled_tokens={'req': 1})
        for size in (2, 4, 8):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as base:
                folder = Path(base) / 'request-A'
                folder.mkdir()
                state.write(folder / 'intent.json', {'cache_capture': {'max_request_snapshot_bytes': 19}})
                budgets = []
                for rank in range(size):
                    rec = Recorder.__new__(Recorder)
                    rec.rank, rec.world_size = rank, size
                    rec.layer_modules = []
                    rec.runner = Mock()
                    rec.coverage = Mock()
                    rec.debugger = Mock()
                    rec.debugger.service.current_iter = 7
                    rec.debugger.service.dump_iter_dir = Path(base) / 'dump' / 'step7'
                    with patch.object(state, 'mapped', return_value=(folder, 'req')):
                        rec.select(schedule)
                    with patch.dict(sys.modules, {'msprobe.pytorch': NS(PrecisionDebugger=Mock())}), \
                            patch.object(state, 'root', return_value=Path(base)):
                        rec.start_probe()
                    self.assertEqual(rec.frame['forward_id'], identity['forward_id'])
                    self.assertEqual(rec.frame['rank'], rank)
                    self.assertEqual(rec.folder, folder / f'rank{rank}')
                    self.assertEqual(rec.frame['dump'], f'dump/step7/rank{rank}')
                    rec.debugger.start.assert_called_once_with(model=rec.runner.get_model(), rank_id=rank)
                    budgets.append(rec.request_remaining)
                self.assertEqual(sum(budgets), 19)
                self.assertLessEqual(max(budgets) - min(budgets), 1)
