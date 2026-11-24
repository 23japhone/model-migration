"""从正式会话离线回读混合 64 层及另一种纯注意力堆栈。"""
from importlib.util import find_spec
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture import test_runtime_closure as closure, native_fixture as native
from worker_capture.session_fixture import write

sys.path.insert(0, str(closure.VERIFY.parent))


@unittest.skipUnless(find_spec('torch'), '需要 CPU PyTorch')
class FullRead(unittest.TestCase):
    setUp = closure.RuntimeClosure.setUp
    start = closure.RuntimeClosure.start
    read = closure.RuntimeClosure.read
    operation = closure.RuntimeClosure.operation
    invoke = closure.RuntimeClosure.invoke

    def collect(self, count, hybrid, stack='layers', budget=1048576, edit=None):
        from worker_capture.cache_cases.full_model import Decoder, execute, inspect
        self.start(cache=True)
        model = Decoder(count, hybrid, stack)
        graph = inspect(model)
        ready = json.loads((self.runtime / 'worker.json').read_text())
        names = [r['path'] for r in graph['layers']]
        ready.update(architecture=graph, layers=names)
        write(self.runtime / 'worker.json', ready)
        modules = [*names, *(s['module'] for r in graph['layers'] for s in r['states'])]
        self.enterContext(patch.object(native, 'MODULES', modules))
        def forward(folder, frame):
            if frame['output_index'] == 1:
                y = execute(model, graph, folder, frame, budget)
                self.assertEqual(y.tolist(), [[1 + count, 2 + count]])
                if edit:
                    edit(frame['decode_cache'])
        original = native.respond
        self.enterContext(patch.object(native, 'respond', lambda root, trace, tokens: original(
            root, trace, tokens, range(20), forward)))
        _, seal = self.invoke(cache_output_index=1)
        return seal

    def test_qwen_layout_all_64_layers_sealed(self):
        receipt = self.operation(self.collect(64, True))['receipt']
        row = self.read(receipt)
        layers = row['decode_cache']['layers']
        self.assertEqual(row['architecture']['counts'], {'attention': 16, 'gdn': 48})
        self.assertEqual(len(layers), 64)
        self.assertTrue(all(l['status'] == 'complete' for l in layers))
        value = self.read(receipt, layer_index=63, cache_snapshot='key.after')
        self.assertEqual(value['cache_snapshot']['values'], [64, 65])
        self.assertEqual(value['forward_id'], row['forward_id'])
        self.assertEqual(self.read(receipt, layer_index=62, cache_snapshot='ssm.after')['cache_snapshot']['values'], [3]*4)
        self.assertEqual(self.read(receipt, layer_index=62, cache_snapshot='output')['cache_snapshot']['values'], [64,65])

    def test_attention_only_alternative_names_and_no_gdn_dependency(self):
        receipt = self.operation(self.collect(3, False, 'h'))['receipt']
        row = self.read(receipt, layer='model.h.2', cache_snapshot='value.after')
        self.assertEqual(row['cache_snapshot']['values'], [-3,-4])
        self.assertEqual(row['architecture']['counts'], {'attention': 3})

    def test_invalid_one_layer_preserves_valid_neighbours(self):
        def corrupt(record):
            state = record['layers'][1]['invocations'][0]['states'][0]
            state['event']['write_plan_matches'] = False
        result = self.operation(self.collect(3, False, 'h', edit=corrupt), expected=1)
        row = self.read(result['receipt'], layer_index=2, cache_snapshot='value.after')
        self.assertEqual([l['status'] for l in row['decode_cache']['layers']], ['complete','incomplete','complete'])
        path = Path(row['cache_snapshot']['path'])
        path.write_bytes(bytes(path.stat().st_size))
        self.operation({'source': result['receipt'], 'request_id': 'sample-37', 'output_index': 1},
                       expected=1, action='capture-read')

    def test_budget_fails_per_layer_and_previous_values_survive(self):
        result = self.operation(self.collect(3, False, 'h', budget=48), expected=1)
        row = self.read(result['receipt'], layer_index=0, cache_snapshot='key.after')
        self.assertEqual(row['cache_snapshot']['values'], [1,2])
        self.assertEqual([l['status'] for l in row['decode_cache']['layers']], ['complete','incomplete','incomplete'])
