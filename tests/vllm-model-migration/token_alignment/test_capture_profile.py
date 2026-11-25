"""单卡文本预测采集不启动张量及缓存观察；默认会话继续采集张量。"""
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))
from verification.runtime.capture import session
from verification.runtime.capture.adapters import state, worker


class TokenProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.arm = {'operation': 'arm', 'source': {'path': '/test', 'sha256': 'a' * 64},
                    'sample_key': 'dataset:4', 'native_id': 4, 'trace_id': 'trace-4',
                    'profile': 'token_prediction'}

    def test_only_single_rank_eager_and_no_cache(self):
        session.validate(self.arm)
        for selection in ({'cache_output_index': 1}, {'profile': 'unknown'}):
            with self.assertRaises(ValueError):
                session.validate({**self.arm, **selection})
        for ready in ({'mode': 'aclgraph'}, {'mode': 'eager', 'rank_workers': [{}]}, {'mode': 'eager'}):
            with self.assertRaises(ValueError):
                session.verify_arm(self.arm, self.root, ready, {})
        session.verify_arm(self.arm, self.root, {'mode': 'eager', 'token_prediction': True}, {})

    def test_worker_starts_and_ends_frame_without_layer_capture(self):
        self.enterContext(patch.dict(os.environ, VERIFY_EAGER_ROOT=str(self.root)))
        folder = self.root / 'case'
        folder.mkdir()
        state.write(self.root / 'active.json', {'directory': folder.name})
        state.write(folder / 'intent.json', self.arm)
        state.write(folder / 'request.json', {'internal_id': 'req1'})
        rec = object.__new__(worker.Recorder)
        rec.runner = SimpleNamespace(input_batch=SimpleNamespace(req_ids=['req1']))
        rec.input_metadata = Mock()
        rec.start_probe = Mock(side_effect=AssertionError('不应写层张量'))
        rec.debugger = Mock()
        rec.coverage = Mock()
        rec.select(SimpleNamespace(num_scheduled_tokens={'req1': 1}))
        rec.start(None, None)
        rec.close()
        rec.input_metadata.assert_called_once()
        rec.start_probe.assert_not_called()
        rec.debugger.stop.assert_not_called()
        rec.coverage.finish.assert_not_called()
        self.assertNotIn('step', rec.frame)
        self.assertNotIn('decode_cache', rec.frame)
        self.assertTrue(list(folder.glob('begin-*.json')))

    def test_real_host_tensor_inplace_mask_and_full_softmax(self):
        try:
            import torch
        except ImportError:
            self.skipTest('本地未安装 PyTorch，其他文件协议测试不依赖 PyTorch')
        rec = object.__new__(worker.Recorder)
        rec.folder, rec.size = self.root, 3
        rec.pending_vectors = {}
        rec.frame = {'profile': 'token_prediction', 'forward_id': 'chunk', 'internal_id':'r', 'output_index':0}
        logits = torch.tensor([[1., 2., 4.]], dtype=torch.bfloat16)
        rec.runner = SimpleNamespace(execute_model_state=SimpleNamespace(logits=logits),
                                     sampler=SimpleNamespace(greedy_sample=lambda x: x.argmax(dim=-1)),
                                     input_batch=SimpleNamespace(sampling_metadata=SimpleNamespace(all_greedy=True)))
        rec.install_sampler()
        # This prefill chunk computes scores but contributes no sampled output to the scheduler.
        rec.logits()
        self.assertEqual(rec.runner.sampler.greedy_sample(logits).item(), 2)
        rec.finish(SimpleNamespace(sampled_token_ids=[[]], req_id_to_index={'r': 0}))
        self.assertFalse(list(self.root.glob('*.bin')))
        self.assertEqual(rec.pending_vectors, {})
        rec.frame = {'profile':'token_prediction', 'forward_id':'step', 'internal_id':'r', 'output_index':0}
        rec.logits()
        logits[:, 2] = -float('inf')
        self.assertEqual(rec.runner.sampler.greedy_sample(logits).item(), 1)
        self.assertFalse(list(self.root.glob('*.bin')))
        rec.finish(SimpleNamespace(sampled_token_ids=[[1]], req_id_to_index={'r': 0}))
        raw = (self.root / rec.frame['raw']['file']).read_bytes()
        self.assertEqual(torch.frombuffer(bytearray(raw), dtype=torch.bfloat16).tolist(), [1., 2., 4.])
        self.assertEqual(rec.frame['candidate'], 1)
        probs = (self.root / rec.frame['processed_probs']['file']).read_bytes()
        values = struct.unpack('<3d', probs)
        self.assertAlmostEqual(values[0], 0.2689414213699951)
        self.assertAlmostEqual(values[1], 0.7310585786300049)
        self.assertEqual(values[2], 0)
        self.assertNotIn('scores', rec.frame)
        self.assertEqual(sorted(p.name for p in self.root.glob('*.bin')), ['step-probs.bin', 'step-raw.bin'])
        self.assertEqual(rec.pending_vectors, {})
