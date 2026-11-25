"""CPU 张量执行与 worker 相同的散页读取及状态行复制寻址。"""
from pathlib import Path
from types import SimpleNamespace as NS
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from worker_capture.test_runtime_closure import VERIFY
sys.path.insert(0,str(VERIFY.parent))
from verification.runtime.capture.adapters.decode_cache.prefix import values
from verification.runtime.capture.adapters.decode_cache.snapshots import Snapshots
try:
    import torch
except ImportError:
    torch = None


@skipUnless(torch is not None, 'requires CPU torch')
class DeviceValues(TestCase):
    def test_noncontiguous_physical_blocks(self):
        cache=torch.arange(16).reshape(4,2,1,2).to(torch.bfloat16)
        plan={'prefix_cache':{'lookup':{'length':4}}}
        group={'blocks':[3,1,2],'page_size':2}
        with TemporaryDirectory() as tmp:
            store=Snapshots(Path(tmp),128,None)
            errors=[]
            with patch.object(store,'boundary',store.save):
                values.save_pages(store,plan,group,[cache],['mla.latent'],errors)
            self.assertFalse(errors)
            result=torch.cat([cache[3],cache[1]]).contiguous().view(torch.uint8).numpy().tobytes()
            self.assertEqual((Path(tmp)/'mla.latent.prefix.bin').read_bytes(),result)
            store.budget.remaining=1
            values.save_pages(store,plan,group,[cache],['key'],errors)
            self.assertIn('预算',errors[0])

    def test_checkpoint_copy_routes_require_real_matching_addresses(self):
        caches=[torch.zeros(4,2,3) for _ in range(2)]
        buf=NS(offset=2,src_ptrs=NS(np=[c[2].data_ptr() for c in caches]),
               dst_ptrs=NS(np=[c[3].data_ptr() for c in caches]),sizes=NS(np=[24,24]))
        rec=NS(runner=NS(_mamba_copy_bufs=buf))
        plan={'prefix_cache':{'lookup':{'length':4,'groups':[[None,{'block':2}]]}}}
        capture=NS(event={'metadata_row':3})
        group={'page_size':2,'group_id':0}
        values.gdn_routes(rec,plan,group,capture,caches)
        self.assertEqual(capture.event['prefix_state']['conv']['copy_ranges'],[[0,0,24]])
        buf.sizes.np[1]=25
        with self.assertRaisesRegex(ValueError,'ssm'):
            values.gdn_routes(rec,plan,group,capture,caches)
