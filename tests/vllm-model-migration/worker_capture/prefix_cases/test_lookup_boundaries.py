"""只接受缓存管理器实际命中的长度和物理驻留身份。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.test_runtime_closure import VERIFY
sys.path.insert(0, str(VERIFY.parent))
from verification.runtime.capture.adapters.decode_cache.prefix.residency import Residency, provenance


class Boundaries(TestCase):
    def setUp(self):
        self.a = NS(block_id=3, block_hash=b'hash', is_null=False)
        self.b = NS(block_id=5, block_hash=b'hash', is_null=False)
        self.null = NS(block_id=0, is_null=True)
        self.blocks = []
        self.length = 0
        self.manager = NS(block_pool=NS(get_new_blocks=lambda n: [], reset_prefix_cache=lambda:False),
                          get_computed_blocks=lambda r:(NS(blocks=[self.blocks]),self.length),
                          get_blocks=lambda req:NS(blocks=[self.blocks+[self.b]]))
        self.tracker = Residency(self.manager)
        self.tracker.target = 'B'
        self.scheduler = NS(_verify_prefix=self.tracker,kv_cache_manager=self.manager)
        self.out = NS(scheduled_new_reqs=[NS(req_id='B')])

    def observe(self, n):
        self.manager.get_computed_blocks(NS(request_id='B'))
        return provenance(self.scheduler,self.out,'B',{'position':n})

    def test_zero_hit_and_final_token_recomputed(self):
        self.assertEqual(self.observe(0)['lookup']['length'],0)
        self.length,self.blocks=4,[self.a]
        self.assertEqual(self.observe(4)['lookup']['length'],4)
        with self.assertRaisesRegex(ValueError,'长度'):
            self.observe(5)

    def test_same_hash_different_selected_block(self):
        self.blocks,self.length=[self.a],4
        self.manager.get_blocks=lambda req:NS(blocks=[[self.b]])
        with self.assertRaisesRegex(ValueError,'实际采用'):
            self.observe(4)

    def test_hybrid_null_placeholders_and_failed_reset(self):
        self.blocks,self.length=[self.null,self.a],8
        first=self.observe(8)
        self.assertIsNone(first['lookup']['groups'][0][0])
        self.manager.block_pool.reset_prefix_cache()
        self.assertEqual(self.observe(8)['epoch'],first['epoch'])
