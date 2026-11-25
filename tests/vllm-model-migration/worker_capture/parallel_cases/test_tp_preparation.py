"""单机纯 TP 的卡数来自设备映射，准备和会话必须使用同一组成员。"""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

VERIFY = next(Path(__file__).resolve().parents[4].glob('skills/*/skills/*/skills/verify-agent'))
sys.path.insert(0, str(VERIFY / 'scripts'))
from verification.runtime.capture import prepare, service
from verification.runtime.capture.adapters.parallel import EXECUTOR_SHA
from verification.runtime.capture.contract import check_service
from verification.evidence import read_bound


def request_for(devices, root):
    return {
        'service': {'ownership': 'owned', 'command': [
            'vllm', 'serve', '--enforce-eager', '--tensor-parallel-size', str(len(devices))]},
        'runtime': {'kind': 'process'}, 'npu': {'devices': devices},
        'worker_capture': {'adapter': 'msprobe_pytorch_eager', 'output_root': str(root)},
        '_device_state': {'devices': [{'physical_id': device, 'visible_index': i, 'logical_id': i}
                                      for i, device in enumerate(devices)]}}


class PreparationTests(unittest.TestCase):
    def test_card_count_and_physical_ids(self):
        for devices in ([4], [3, 4], [7, 2, 5], [7, 3, 0, 4], list(reversed(range(8)))):
            with self.subTest(devices=devices), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                request = request_for(devices, root)
                receipt = {}
                prepare.prepare_files(request, root / 'attempt', receipt)
                if len(devices) == 1:
                    self.assertNotIn('parallel', receipt)
                else:
                    self.assertEqual(read_bound(receipt['parallel']), {
                        'world_size': len(devices), 'devices': request['_device_state']['devices']})

    def test_tp_size_must_match_unique_physical_cards_and_execution_mode(self):
        for devices, flags in (([], []), ([1, 2, 2, 4], []), ([0, 2, 3, 4], ['--tensor-parallel-size=2']),
                               ([4, 3, 2, 1], ['--pipeline-parallel-size=2']),
                               ([4, 3, 2, 1], ['--distributed-executor-backend=ray']),
                               ([4, 3, 2, 1], ['--enable-expert-parallel'])):
            request = request_for(devices, '/work/out')
            request['service']['command'].extend(flags)
            with self.subTest(devices=devices, flags=flags), self.assertRaises(ValueError):
                check_service(request)

    def test_live_reads_each_declared_role(self):
        for size in (2, 4, 8):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp).resolve()
                request = request_for(list(reversed(range(size))), root)
                receipt = {}
                prepare.prepare_files(request, root / 'attempt', receipt)
                devices = read_bound(receipt['parallel'])['devices']
                members = {f'worker-rank{i}': dict(row, rank=i, tp_rank=i, world_size=size,
                           group_ranks=list(range(size)), pid=100+i, namespace='fixture', layers=['l0'],
                           architecture={}) for i, row in enumerate(devices)}
                records = {**members, 'executor': {'world_size': size, 'output_rank': 0, 'sha256': EXECUTOR_SHA},
                           'engine': {}, 'frontend': {}}
                handoff = {'resource': {}, 'workers': {'listener_pids': [23]}, 'endpoint': 'http://localhost:9000'}
                stack.enter_context(patch.object(service.carrier, 'validate'))
                stack.enter_context(patch.object(service.inspection, 'confirm_workers'))
                stack.enter_context(patch.object(service.inspection, 'listener_owners', return_value=[23]))
                stack.enter_context(patch.object(service, 'verify_sources'))
                stack.enter_context(patch.object(service, 'verified_role',
                                                 side_effect=lambda root, h, p, role, roles: records[role]))
                directory = Path(receipt['parallel']['path']).parent
                for role in members:
                    (directory / f'{role}.json').touch()
                ready = service.live(handoff, receipt, root)
                self.assertEqual(ready['rank_workers'], list(members.values()))
                extra_role = f'worker-rank{size}'
                (directory / f'{extra_role}.json').touch()
                with self.assertRaisesRegex(ValueError, extra_role):
                    service.live(handoff, receipt, root)
