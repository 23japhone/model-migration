"""通过公开 CLI 封存、查询任意物理卡号的单机 TP。仅设备/worker 观察由夹具提供。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.parallel_cases.cache_fixture import ARCH, LAYER, cache_witness
from worker_capture.parallel_cases.frames_fixture import add_frame
from worker_capture.session_fixture import AGENT, BUNDLE, deployment, isolated_bundle, public, write
sys.path.insert(0, str(BUNDLE / AGENT / 'scripts'))
from verification.runtime.capture.adapters.parallel import EXECUTOR_SHA


class ParallelSessionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.verify = isolated_bundle(self.root)

    def operation(self, request, *, action='capture-session', expected=0):
        proc, result = public(self.verify, self.root, request, action)
        self.assertEqual(proc.returncode, expected, proc.stderr + str(result)[:2000])
        return result

    def arm(self, physical):
        size = len(physical)
        report, runtime = deployment(self.root, 'http://localhost:1', cache=True, physical_devices=physical)
        worker = json.loads((runtime / 'worker.json').read_bytes())
        worker['decode_cache'].update(full_request=True, max_snapshot_bytes=65536)
        for rank, device in enumerate(physical):
            write(runtime / f'worker-rank{rank}.json', {
                **worker, 'architecture': ARCH, 'layers': [LAYER], 'state_shards': {},
                'pid': rank + 101, 'namespace': 'fixture', 'visible_index': rank,
                'logical_id': rank, 'physical_id': device, 'rank': rank, 'tp_rank': rank,
                'world_size': size, 'group_ranks': list(range(size))})
        write(runtime / 'executor.json', {'output_rank': 0, 'world_size': size, 'sha256': EXECUTOR_SHA})
        session = self.operation({'operation': 'begin', 'source': report, 'owner_pid': os.getpid()})['session']
        armed = self.operation({'operation': 'arm', 'sample_key': 'test', 'native_id': 0, 'source': session,
                                'trace_id': 'A', 'cache_capture': {'mode': 'full_request', 'max_request_snapshot_bytes': 65536}})
        return session, runtime, runtime / armed['directory']

    def seal(self, physical, change_witness=None, *, expected=0):
        session, runtime, folder = self.arm(physical)
        ids = [add_frame(runtime, folder, len(physical), step, pos, count, ix, cache_witness)
               for step, (pos, count, ix) in enumerate(((0, 2, None), (2, 1, 0), (3, 1, 1)))]
        if change_witness:
            change_witness(folder, ids)
        prompt, tokens = [7, 7, 7], [7, 7]
        write(folder / 'request.json', {'prompt_ids': prompt, 'internal_id': 'req', 'external_id': 'ext'})
        write(folder / 'frontend.json', {'trace_id': 'A', 'external_id': 'ext', 'response_id': 'reply'})
        write(folder / 'frontend-end.json', {'trace_id': 'A', 'request_wall_seconds': 1})
        write(folder / 'engine-output.json', {'external_id': 'ext', 'prompt_ids': prompt, 'finish_reason': 'length', 'token_ids': tokens})
        http = {'request': write(folder / 'request.raw', {'prompt': prompt}),
                'response': write(folder / 'response.raw', {'id': 'reply', 'choices': [{'token_ids': tokens}]})}
        result = self.operation({'operation': 'seal', 'source': session, 'http': http, 'trace_id': 'A'}, expected=expected)
        self.operation({'operation': 'end', 'source': session})
        return result, ids

    def read(self, seal, **selector):
        return self.operation({'source': seal['receipt'], 'request_id': 'A', **selector}, action='capture-read')

    def test_four_ranks_query_every_shard_and_reject_out_of_range(self):
        self.check_complete([7, 2, 5, 1])

    def test_eight_ranks_query_every_shard_and_reject_out_of_range(self):
        self.check_complete([8, 6, 2, 5, 1, 10, 3, 0])

    def check_complete(self, devices):
        sealed, ids = self.seal(devices)
        self.assertEqual(sealed['cache_status'], 'complete')
        timeline = self.read(sealed, view='timeline')['timeline']
        self.assertEqual([entry['forward_id'] for entry in timeline], ids)
        self.assertEqual([entry['output_index'] for entry in timeline], [None, 0, 1])
        by_rank = self.read(sealed, prompt_position=0)['ranks']
        self.assertEqual([r['device']['physical_id'] for r in by_rank], devices)
        for rank in range(len(devices)):
            row = self.read(sealed, output_index=1, rank=rank, layer_index=0, cache_snapshot='key.after')
            self.assertEqual((row['forward_id'], row['input_ids'], row['rank']), (ids[2], [7], rank))
            self.assertEqual(row['cache_snapshot']['values'], [rank * 100 + 3])
            self.assertTrue(row['calls'])
            self.assertIn(f'rank{rank}', Path(row['calls'][0]['tensors']['output'][0]['path']).parts)
        invalid = {'source': sealed['receipt'], 'request_id': 'A', 'output_index': 1, 'rank': len(devices)}
        proc, _ = public(self.verify, self.root, invalid, 'capture-read')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('rank', proc.stderr + proc.stdout)

        # 保持路径、摘要均合法，但把 rank 2 的设备记录错绑到另一张物理卡。
        receipt = Path(sealed['receipt']['path'])
        result = json.loads(receipt.read_bytes())
        result['timeline'][-1]['ranks'][2]['device']['physical_id'] = devices[0]
        copy = {'receipt': write(receipt.with_name('rebound-receipt.json'), result)}
        invalid = self.read(copy, output_index=1, rank=2)
        self.assertEqual(invalid['status'], 'incomplete')
        self.assertIn('设备身份', ' '.join(invalid['errors']))
        self.assertEqual(self.read(copy, output_index=1, rank=3)['status'], 'complete')

    def test_eight_ranks_read_back_native_tensor_and_handle_late_file_loss(self):
        # 公开目录的原生文件交给 PyTorch 自身反序列化。后端执行与 msProbe 调用在本测试中仍为合成。
        import importlib.util
        if importlib.util.find_spec('torch') is None:
            self.skipTest('读取 PyTorch 原生张量的复核需要 CPU PyTorch')
        import torch

        def native_outputs(folder, ids):
            for rank in range(8):
                native = folder.parent / 'dump/native/step2' / f'rank{rank}/dump_tensor_data'
                output = native / ('Module.' + LAYER + '.Block.forward.0-output.pt')
                torch.save(torch.tensor([[rank, 7]], dtype=torch.float32), output)

        sealed, ids = self.seal(list(reversed(range(8))), native_outputs)
        self.assertEqual(sealed['cache_status'], 'complete')
        self.assertEqual(self.read(sealed, view='timeline', rank=7)['timeline'][-1]['ranks'][0]['rank'], 7)
        row = self.read(sealed, output_index=1, rank=7, layer_index=0, cache_snapshot='key.after')
        layer_call, = [c for c in row['calls'] if c['module'] == LAYER]
        path = layer_call['tensors']['output'][0]['path']
        self.assertEqual(torch.load(path, weights_only=True).tolist(), [[7, 7]])
        self.assertEqual(row['cache_snapshot']['values'], [703])
        Path(row['cache_snapshot']['path']).unlink()
        failed = self.read(sealed, output_index=1, rank=7, layer_index=0, cache_snapshot='key.after')
        self.assertEqual(failed['status'], 'incomplete')
        healthy = self.read(sealed, forward_id=ids[-1], rank=6, layer_index=0, cache_snapshot='key.after')
        self.assertEqual((healthy['status'], healthy['cache_snapshot']['values']), ('complete', [603]))

    def test_eight_ranks_preserve_unaffected_shards(self):
        def damage(folder, ids):
            (folder / 'member-rank3.json').write_text('{}')
            (folder / 'rank5' / f'end-{ids[2]}.json').unlink()
            path = folder / 'rank4' / f'end-{ids[2]}.json'
            frame = json.loads(path.read_bytes())
            write(path, {**frame, 'dump': frame['dump'].replace('rank4', 'rank1')})
        sealed, ids = self.seal([8, 6, 2, 5, 1, 10, 3, 0], damage, expected=1)
        self.assertEqual(sealed['cache_status'], 'incomplete')
        summary = self.read(sealed, output_index=1)
        failed = [r['rank'] for r in summary['ranks'] if r['status'] == 'incomplete']
        self.assertEqual(failed, [3, 4, 5])
        self.assertTrue(any('rank3' in e for e in sealed['cache_errors']))
        self.assertTrue(any(ids[-1] in e and 'rank5' in e for e in sealed['cache_errors']))
        row = self.read(sealed, forward_id=ids[-1], rank=7, layer_index=0, cache_snapshot='key.after')
        self.assertEqual((row['status'], row['request_status'], row['cache_snapshot']['values']), ('complete', 'incomplete', [703]))
        self.assertEqual(len(sealed['capture_totals']['by_rank']), 8)
