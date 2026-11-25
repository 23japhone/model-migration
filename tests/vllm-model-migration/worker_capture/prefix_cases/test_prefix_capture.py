"""覆盖显式启用、真实查找及物理块重新分配。"""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.test_runtime_closure import VERIFY
sys.path.insert(0, str(VERIFY.parent))
from verification.runtime.capture import session


class PrefixTests(unittest.TestCase):
    def test_explicit_arm(self):
        request = dict(source={'path': '/a', 'sha256': '0'*64}, operation='arm',
                       sample_key='a', native_id=1, trace_id='a')
        cache = dict(mode='full_request', max_request_snapshot_bytes=20, prefix_reuse=True)
        session.validate(dict(request, cache_capture=cache))
        with self.assertRaises(ValueError):
            session.validate(dict(request, cache_capture=dict(cache, prefix_reuse=1)))

    def test_recycled_block_and_reset_have_distinct_identity(self):
        from verification.runtime.capture.adapters.decode_cache.prefix.residency import Residency
        block = NS(block_id=3, block_hash=b'key', is_null=False)
        pool = NS(get_new_blocks=lambda n: [block], reset_prefix_cache=lambda: True)
        manager = NS(block_pool=pool, get_computed_blocks=lambda req: (NS(blocks=([block],)), 4))
        tracker = Residency(manager)
        tracker.target = 'B'
        manager.get_computed_blocks(NS(request_id='B'))
        first = tracker.lookup['groups'][0][0]
        self.assertEqual((first['block'], first['hash']), (3, b'key'.hex()))
        pool.get_new_blocks(1)
        self.assertNotEqual(first, tracker.block(block))
        second = tracker.block(block)
        pool.reset_prefix_cache()
        self.assertNotEqual(second, tracker.block(block))
