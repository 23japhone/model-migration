"""逐层回查必须保留同名缓存的层坐标和全前向字节预算。"""
from pathlib import Path
import sys
import tempfile
import unittest

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))


class AllLayers(unittest.TestCase):
    def test_scoped_snapshots_share_budget_without_filename_collisions(self):
        import torch
        from verification.runtime.capture.adapters.decode_cache.snapshots import Snapshots
        with tempfile.TemporaryDirectory() as root:
            snapshots = Snapshots(Path(root), 8, None)
            one = snapshots.scope('layer-0')
            two = snapshots.scope('layer-1')
            one.save('key.after', torch.tensor([1.]))
            two.save('key.after', torch.tensor([2.]))
            self.assertNotEqual(one.files['key.after']['sha256'], two.files['key.after']['sha256'])
            with self.assertRaises(ValueError):
                two.save('value.after', torch.tensor([3.]))
