"""使用原生 msProbe 文件布局复核同层重复调用、重复 Token、漏写和摘要。"""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))
from verification.runtime.capture import index
from verification.runtime.capture.tensors import file_ref
from verification.runtime.capture.tensors import inventory
from verification.runtime.capture.adapters.coverage import verify


class Rows(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.folder = self.root / 'request'
        self.folder.mkdir()
        self.dump = self.root / 'dump/native/step4/rank0'
        self.files = self.dump / 'dump_tensor_data'
        self.files.mkdir(parents=True)
        self.layer = 'model.layers.1'
        self.module = 'Module.model.layers.1.Block.forward.0'
        self.apis = ['Torch.add.3.forward', 'Torch.add.4.forward']
        self.save(self.dump / 'construct.json', {self.module: None, **dict.fromkeys(self.apis, self.module)})
        data = {}
        for call in [self.module, *self.apis]:
            data[call] = {}
            for side in ('input_args', 'output'):
                name = f'{call}.{side}.0.pt'
                (self.files / name).write_bytes(name.encode())
                data[call][side] = [{'dtype': 'torch.float32', 'shape': [1, 2], 'data_name': name}]
        data[self.apis[1]]['output'].append({'type': 'torch.Tensor', 'dtype': 'torch.float32', 'shape': [0]})
        self.save(self.dump / 'dump.json', {'data': data})
        self.save(self.folder / 'intent.json', {'request_id': 'client'})
        self.mapped = {'prompt_ids': [11,22,33], 'internal_id': 'cmpl-client-0-a', 'external_id': 'cmpl-client-0'}
        self.ready = {'layers': [self.layer], 'vocab_size': 4, 'rank': 0}
        frame = {'internal_id': self.mapped['internal_id'], 'output_index': 1, 'forward_id': 'decode',
                 'input_ids': [2], 'positions': [3], 'logits_indices': [0], 'batch_row': 0, 'step': 4,
                 'dump': 'dump/native/step4/rank0', 'candidate': 2}
        for name, values in [('raw', [0,5,2,1]), ('scores', [0,-float('inf'),2,1])]:
            (self.folder / name).write_bytes(struct.pack('<4f', *values))
            frame[name] = {'file': name, 'size': 4, 'dtype': 'torch.float32'}
        self.save(self.folder / 'decode.json', frame)
        self.save(self.folder / 'prediction-1.json', frame)
        self.save(self.folder / 'accepted-1.json', {'internal_id': frame['internal_id'], 'output_index': 1,
                  'forward_id': 'decode', 'token_id': 2, 'finish_reason': 'length'})

    @staticmethod
    def save(path, data):
        path.write_text(json.dumps(data))

    def receipt(self):
        row = index.prediction(self.folder, self.root, self.ready, self.mapped, [2, 2], 1)
        path = self.folder / 'receipt.json'
        ref = file_ref(self.folder / 'intent.json', self.folder)
        self.save(path, {'request_id': 'client', 'outputs': [None, row], 'token_ids': [2, 2], 'derived_config': ref, 'worker': ref, 'engine': ref,
                         'sampling': ref, 'engine_input': ref, 'response': ref, 'response_id': 'cmpl-client'})
        return file_ref(path, self.folder)

    def test_repeated_token_and_two_api_calls(self):
        row = index.selected(index.query(self.receipt(), 'client', 1),
                             {'layer': self.layer, 'api': 'Torch.add', 'occurrence': 1})
        self.assertEqual(row['prefix_ids'], [11,22,33,2])
        self.assertEqual(row['raw']['values'], [0,5,2,1])
        self.assertEqual(row['scores']['values'][1], '-inf')
        self.assertEqual(row['calls'][-1]['tensors']['output'][-1]['elements'], 0)
        self.assertEqual([c['call'] for c in row['calls']], [self.module, self.apis[1]])

    def test_digest_changes_and_missing_files(self):
        receipt = self.receipt()
        file = next(self.files.iterdir())
        file.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'decode'):
            index.query(receipt, 'client', 1)
        file.unlink()
        with self.assertRaisesRegex(ValueError, '文件缺失'):
            self.receipt()

    def test_early_eos(self):
        with self.assertRaisesRegex(ValueError, '缺少输出'):
            index.query(self.receipt(), 'client', 2)

    def test_worker_and_engine_mismatch(self):
        p = self.folder / 'accepted-1.json'
        self.save(p, {**json.loads(p.read_text()), 'forward_id': 'prefill'})
        with self.assertRaisesRegex(ValueError, '身份不符'):
            self.receipt()

    def test_native_none_and_empty_returns_are_explicit_values(self):
        data = json.loads((self.dump/'dump.json').read_text())
        names = list(data['data'])
        observed = {'calls': {'input': names, 'output': names},
                    'modules': {'begin': [self.layer], 'end': [self.layer]}, 'scope': 'registered'}
        for output, expected in [([None], None), (None, None), ([], []), ({}, {})]:
            with self.subTest(output=output):
                data['data'][self.apis[0]]['output'] = output
                self.save(self.dump/'dump.json', data)
                calls = inventory(self.dump, [self.layer], 0, [3])
                values = [v['value'] for v in calls[1]['values'] if v['argument'][0] == 'output']
                self.assertEqual(values, [expected])
                self.assertEqual(verify(observed, calls)['status'], 'complete_within_scope')

    def test_missing_output_is_not_hidden_by_input_literals(self):
        data = json.loads((self.dump/'dump.json').read_text())
        call = data['data'][self.apis[0]]
        del call['output']
        call['input_args'].append({'type': 'slice', 'value': [None, 1, None]})
        self.save(self.dump/'dump.json', data)
        with self.assertRaisesRegex(ValueError, '输入输出'):
            inventory(self.dump, [self.layer], 0, [3])
