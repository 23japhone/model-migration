"""MLA 组件、跨物理页的计划和公开快照选择。"""
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))
from verification.runtime.capture import cache_evidence
from verification.runtime.capture.adapters.architecture import cache_component


class MLATests(unittest.TestCase):
    def test_actual_implementation_selects_backend(self):
        cls = type('AscendMLAImpl', (), {'__module__': 'vllm_ascend.attention.mla_v1'})
        layer = SimpleNamespace(impl=cls(), layer_name='model.layers.26.self_attn.attn')
        self.assertEqual(cache_component('wrapped.module', layer)['kind'], 'mla')
        layer.impl = object()
        self.assertEqual(cache_component('wrapped.module', layer)['kind'], 'unsupported')

    def test_public_selectors(self):
        for key in ('mla.latent.after', 'mla.rope.after', 'mla.projection.input'):
            cache_evidence.validate_snapshot({'cache_snapshot': key})

    def test_graph_and_parallel_capabilities(self):
        from verification.runtime.capture.adapters.decode_cache.runtime import validate_backend
        for config in ({'world_size': 2}, {'mode': 'aclgraph'}):
            with self.assertRaisesRegex(ValueError, '单卡 Eager'):
                validate_backend(SimpleNamespace(impl=object()), 'mla', **config)
        impl = SimpleNamespace(vllm_config=SimpleNamespace(kv_transfer_config=object()))
        with self.assertRaisesRegex(ValueError, 'KV 传输'):
            validate_backend(SimpleNamespace(impl=impl), 'mla')

    def capture(self, start, count, prefill, **kwargs):
        if not find_spec('torch'):
            self.skipTest('需要 CPU PyTorch')
        from tempfile import TemporaryDirectory
        from worker_capture.cache_cases.mla_fixture import run
        folder = self.enterContext(TemporaryDirectory())
        return run(folder, start, count, prefill, **kwargs)

    def test_cross_page_and_paged_decode(self):
        event, files, result = self.capture(127, 2, True)
        self.assertEqual(result['errors'], [])
        self.assertEqual(event['write_slots'], [511, 128])
        self.assertEqual(event['history'][0]['length'], 127)
        self.assertEqual(files['mla.latent.after']['sha256'], files['mla.latent.output']['sha256'])
        event, files, result = self.capture(130, 1, False)
        self.assertEqual(result, {'errors': [], 'ops': ['mla.write', 'mla.decode']})
        self.assertEqual(event['decode']['blocks'], [3, 1])
        self.assertEqual(event['decode']['kv_length'], 131)
        self.assertNotIn('mla.latent.output', files)

    def test_cold_and_missing_history(self):
        self.assertEqual(self.capture(0, 2, True)[2]['errors'], [])
        self.assertIn('历史范围缺失', ' '.join(self.capture(128, 2, True, history=False)[2]['errors']))

    def test_wrong_slot_budget_and_swapped_components(self):
        from verification.runtime.capture.adapters.decode_cache.backends.mla_evidence import verify_components
        event, files, result = self.capture(130, 1, False, wrong_slot=True)
        self.assertIn('写槽不同', ' '.join(result['errors']))
        self.assertIn('mla.latent.after', files)
        files['mla.latent.after'], files['mla.rope.after'] = files['mla.rope.after'], files['mla.latent.after']
        with self.assertRaisesRegex(ValueError, '布局不符'):
            verify_components(event, files, 1)
        _, files, result = self.capture(130, 1, False, budget=20)
        self.assertEqual(set(files), {'mla.projection.input', 'mla.latent.after'})
        self.assertIn('max_snapshot_bytes', ' '.join(result['errors']))
        _, files, result = self.capture(130, 1, False, budget=4)
        self.assertEqual(set(files), {'mla.rope.after'})
        self.assertIn('mla.projection.input', ' '.join(result['errors']))
