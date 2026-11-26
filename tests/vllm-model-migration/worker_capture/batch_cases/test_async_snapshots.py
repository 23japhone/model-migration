"""文件延迟写入时覆盖原缓冲区，仍可恢复逐前向的原始 BF16。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import torch

sys.path.insert(0, str(next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py')).parent))
from verification.runtime.capture.adapters.async_batch.snapshots import Archive, Exporter, FrozenSnapshots, publish
from verification.runtime.capture.adapters.decode_cache.snapshots import SnapshotBudget


class FrozenValues(unittest.TestCase):
    def test_old_forward_value_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            exporter = Exporter()
            self.addCleanup(exporter.pool.shutdown)
            tensor = torch.tensor([11,17],dtype=torch.bfloat16)
            records = []
            for fid in ('old','new'):
                archive = Archive(exporter)
                dest = folder / fid
                dest.mkdir()
                store = FrozenSnapshots(dest, SnapshotBudget(4), None, archive, 'A')
                expected = tensor.view(torch.uint8).numpy().tobytes()
                item = store.save('key.after', tensor)
                self.assertRaises(ValueError, store.save, 'value.after', tensor)
                records.append((fid,archive,item,expected))
                tensor.add_(4)
            for fid,archive,item,raw in reversed(records):
                publish(folder, {'forward_id':fid,'member_errors':{}}, archive)
                self.assertEqual((folder / fid / item['file']).read_bytes(),raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
                self.assertGreater(json.loads((folder / ('numeric-'+fid+'.json')).read_bytes())['archived_ns'],0)
