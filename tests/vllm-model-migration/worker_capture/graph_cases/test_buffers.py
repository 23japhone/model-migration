"""设备快照和图原生张量文件的可验证边界。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/scripts'))
from verification.runtime.capture.aclgraph.native import tensor_header
from verification.runtime.capture.adapters.aclgraph.observations import GraphObservations, valid_view
from verification.runtime.capture.adapters.aclgraph.replay import capturing


class BufferTests(unittest.TestCase):
    def test_device_budget_counts_allocations_across_captured_graphs(self):
        import torch
        obs = GraphObservations.__new__(GraphObservations)
        obs.device_limit = 16
        obs.allocated = 0
        tensor = torch.arange(4,dtype=torch.float32)
        for layer in (0,1):
            binding = {'buffers':{},'errors':[]}
            token = capturing.set(binding)
            try:
                obs.record(tensor,layer,'input')
            finally:
                capturing.reset(token)
            if layer:
                self.assertIn('1/input',str(binding['errors']))
                self.assertFalse(binding['buffers'])
            else:
                saved = binding['buffers'][(0,'input')]
                tensor.add_(1)
                self.assertEqual(valid_view(saved,'input',2).tolist(),[0,1])
        self.assertEqual(obs.allocated,16)

    def test_same_buffer_replayed_twice_before_export(self):
        obs = GraphObservations.__new__(GraphObservations)
        obs.used = set()
        obs.pending = []
        obs.frame = {'errors':[]}
        source = dict(graph_id='one',ordinal=1,binding={})
        obs.replayed(source)
        obs.replayed({**source,'ordinal':2})
        self.assertIn('覆盖',str(obs.frame['errors']))
        self.assertFalse(obs.pending, '已覆盖的数据不能挂在第一次 replay 下')

    def test_real_torch_zip_header_requires_complete_storage(self):
        import torch
        with TemporaryDirectory() as work:
            original, damaged = (Path(work)/name for name in ('original.pt','damaged.pt'))
            torch.save(torch.zeros(2,3,dtype=torch.bfloat16),original)
            metadata = tensor_header(original)
            self.assertEqual((metadata['shape'],metadata['dtype'],metadata['data_bytes']),([2,3],'torch.bfloat16',12))
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(damaged,'w') as dest:
                for entry in source.infolist():
                    data = source.read(entry)
                    dest.writestr(entry.filename, data[:-1] if entry.filename.endswith('/data/0') else data)
            with self.assertRaisesRegex(ValueError,'字节数'):
                tensor_header(damaged)
