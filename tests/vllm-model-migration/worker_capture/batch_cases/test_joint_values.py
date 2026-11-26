"""用相同 Token 和不同 BF16 行核对公开查询、摘要与成员隔离。"""

import json
from pathlib import Path
import sys
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.batch_cases import test_public_batch as fixtures
from worker_capture.native_fixture import native_dump
from worker_capture.session_fixture import write


class MemberValues(unittest.TestCase):
    def setUp(self):
        base = self.base = fixtures.BatchSession()
        base.profile = 'batch_joint'
        base.prompts = dict(A=[71], B=[71])
        base.setUp()
        self.addCleanup(base.doCleanups)
        path = base.runtime / 'worker.json'
        ready = json.loads(path.read_bytes())
        self.descriptors = [dict(index=i, path=name, states=[]) for i, name in enumerate(ready['layers'])]
        write(path, {**ready, 'architecture': {'layers': self.descriptors}})

    def capture(self, missing_trace=None, missing_activation=False):
        base = self.base
        fid = base.step(0, ['B', 'A'], [0, 0], [1, 1], {'A': [71], 'B': [71]}, [0, 0])
        worker = json.loads((base.group / ('worker-' + fid + '.json')).read_bytes())
        dump, names = native_dump(base.runtime, 0)
        meta = json.loads((dump / 'dump.json').read_bytes())
        for call in meta['data'].values():
            for side in call.values():
                item = side[0]
                item.update(shape=[2, 2], dtype='torch.bfloat16')
                tensor = torch.tensor([[17, 19], [23, 29]], dtype=torch.bfloat16)
                torch.save(tensor, dump / 'dump_tensor_data' / item['data_name'])
        if missing_activation:
            (dump / 'dump_tensor_data' / 'Module.model.layers.0.Block.forward.0-output.pt').unlink()
        write(dump / 'dump.json', meta)
        for row in worker['rows']:
            folder = base.group / ('member-' + row['trace_id'])
            path = folder / (fid + '-logits.bin')
            if row['trace_id'] != missing_trace:
                path.write_bytes(b'\x00' * 400)
            row['logits'] = dict(file=path.name, size=100, dtype='torch.float32')
        from verification.runtime.capture.tensors import module_path

        modules = [module_path(n) for n in names if module_path(n)]
        coverage = dict(
            scope='all_invoked_modules_and_msprobe_registered_apis',
            calls=dict(input=names, output=names),
            modules=dict(begin=modules, end=modules),
        )
        write(
            base.group / ('numeric-' + fid + '.json'),
            dict(
                **worker,
                profile='batch_joint',
                errors=[],
                positions=[0, 0],
                signatures={d['path']: ['hidden_states'] for d in self.descriptors},
                dump=str(dump.relative_to(base.runtime)),
                step=0,
                coverage=coverage,
            ),
        )
        return {key: base.seal(key, [71], code=int(key == missing_trace or missing_activation))['receipt'] for key in ('A', 'B')}

    def test_missing_original_at_seal_preserves_other_side_of_same_call(self):
        refs = self.capture(missing_activation=True)
        request = dict(source=refs['A'], request_id='A', layer_index=0, output_index=0,
                       native_tensor=dict(call='Module.model.layers.0.Block.forward.0', argument=['input_args', 0]))
        row = self.base.cli(request, 'capture-read')
        self.assertEqual(row['status'], 'incomplete')
        self.assertEqual(row['native_tensor']['shape'], [1, 2])

    def test_missing_member_logits_keeps_peer_complete(self):
        refs = self.capture(missing_trace='A')
        for key, status in [('A', 'incomplete'), ('B', 'completed')]:
            row = self.base.cli(dict(source=refs[key], request_id=key, layer_index=1, output_index=0), 'capture-read')
            self.assertEqual(row['status'], status)
            self.assertTrue(row['calls'])
            if key == 'A':
                self.assertIn('logits', str(row['errors']))

    def test_native_slices_and_source_tampering(self):
        refs = self.capture()

        def get(key, **selector):
            return self.base.cli(
                dict(source=refs[key], request_id=key, output_index=0, layer_index=0, **selector), 'capture-read'
            )

        row = get('A')
        source = next(c for c in row['calls'] if c['module'] == row['layer'])
        select = dict(native_tensor=dict(call=source['call'], argument=['output', 0]))
        for key, values in [('A', [23, 29]), ('B', [17, 19])]:
            item = get(key, **select)['native_tensor']
            arr = torch.frombuffer(bytearray(Path(item['path']).read_bytes()), dtype=torch.bfloat16)
            self.assertEqual(arr.tolist(), values)
            self.assertEqual(item['shape'], [1, 2])
        unknown = next(c for c in row['calls'] if c['module'] is None)
        hidden = get('B', native_tensor=dict(call=unknown['call'], argument=['output', 0]))
        self.assertEqual(hidden['status'], 'incomplete')
        self.assertNotIn('native_tensor', hidden)
        dump = self.base.runtime / 'dump/native/step0/rank0/dump_tensor_data'
        (dump / (source['call'] + '-output.pt')).write_bytes(b'bad')
        self.assertEqual(get('A', **select)['status'], 'incomplete')
        select['native_tensor']['argument'] = ['input_args', 0]
        self.assertIn('native_tensor', get('A', **select))
