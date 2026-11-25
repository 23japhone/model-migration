"""通过公开 arm、seal、capture-read 重验两层数值和前向身份。"""
from importlib.util import find_spec
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture import native_fixture as native
from worker_capture import test_runtime_closure as closure
from worker_capture.session_fixture import write

sys.path.insert(0, str(closure.VERIFY.parent))


@unittest.skipUnless(find_spec('torch'), '实际数值观测测试需要 CPU PyTorch')
class DecodeRead(unittest.TestCase):
    setUp = closure.RuntimeClosure.setUp
    start = closure.RuntimeClosure.start
    read = closure.RuntimeClosure.read
    operation = closure.RuntimeClosure.operation
    invoke = closure.RuntimeClosure.invoke

    def configure(self, forward=None):
        from worker_capture.cache_cases import runner
        self.start(cache=True)
        ready = json.loads((self.runtime / 'worker.json').read_text())
        ready.update(layers=['model.layers.0', 'model.layers.3'])
        write(self.runtime / 'worker.json', ready)
        modules = [*ready['layers'], runner.FULL, runner.LINEAR]
        self.enterContext(patch.object(native, 'MODULES', modules))
        original = native.respond
        self.enterContext(patch.object(native, 'respond', lambda root, trace, tokens: original(root, trace, tokens, range(20), forward)))
        self.tokens = [16, 17]

    def collect(self, budget=65536, slot=52, row=7, edit_record=None, last_read_block=13):
        from worker_capture.cache_cases.runner import Decode, Watch
        try:
            from mindie_debugger.precision import MemoryWatchpoint
        except ImportError:
            MemoryWatchpoint = Watch
        def forward(folder, frame):
            if frame['output_index'] == 1:
                decode = Decode(folder, frame, budget, MemoryWatchpoint)
                decode.run(slot, row, last_read_block)
                self.assertNotIn('_forward_core', vars(decode.linear))
                self.assertNotIn('reshape_and_cache', vars(decode.impl))
                if edit_record:
                    edit_record(frame['decode_cache'])
        self.configure(forward)
        _, seal = self.invoke(cache_output_index=1)
        self.assertEqual(len(self.calls), 1)
        return seal

    def test_read_token_with_verified_cache_bytes(self):
        seal = self.collect()
        receipt = self.operation(seal)['receipt']
        self.operation({'operation': 'end', 'source': self.session})
        result = self.read(receipt)
        cache = result['decode_cache']
        self.assertEqual((result['token_id'], result['input_ids'], result['positions']), (17, [16], [20]))
        self.assertEqual((cache['forward_id'], cache['step']), (result['forward_id'], result['step']))
        self.assertEqual(cache['attention']['read_slot_ranges'][-1], [52, 53])
        self.assertEqual(cache['attention']['expected_read_slot_ranges'], cache['attention']['read_slot_ranges'])
        self.assertTrue(cache['attention']['read_plan_matches'])
        self.assertEqual(cache['attention']['write_slot'], 52)
        self.assertIn('layers.3.self_attn.attn', cache['attention']['msprobe_call'])
        self.assertEqual(cache['gdn']['expected_row'], 7)
        values = {k: v['preview'] for k, v in cache['snapshots'].items()}
        self.assertEqual((values['key.after'], values['value.after']), ([1, 2], [3, 4]))
        self.assertEqual((values['conv.before'], values['conv.after']), ([1]*3, [3]*3))
        self.assertEqual((values['ssm.before'], values['ssm.after']), ([1.5]*4, [4.5]*4))
        for name in ('key.after', 'value.after', 'conv.before', 'conv.after', 'ssm.before', 'ssm.after'):
            numeric = self.read(receipt, cache_snapshot=name)['cache_snapshot']
            self.assertEqual(numeric['values'], values[name])
            self.assertEqual(numeric['shape'], cache['snapshots'][name]['shape'])
        path = Path(cache['snapshots']['ssm.after']['path'])
        self.assertEqual(struct.unpack('<4f', path.read_bytes()), (4.5,)*4)
        self.assertNotIn('decode_cache', self.read(receipt, output_index=0))
        path.write_bytes(bytes(16))
        self.operation({'source': receipt, 'request_id': 'sample-37', 'output_index': 1}, expected=1, action='capture-read')

    def test_different_valid_written_slots_are_diagnostic_evidence(self):
        receipt = self.operation(self.collect(slot=53, row=6, last_read_block=14))['receipt']
        cache = self.read(receipt)['decode_cache']
        self.assertFalse(cache['attention']['write_plan_matches'])
        self.assertFalse(cache['attention']['read_plan_matches'])
        self.assertEqual(cache['attention']['read_slot_ranges'][-1], [56, 57])
        self.assertFalse(cache['attention']['expected_key']['changed'])
        self.assertTrue(cache['attention']['key']['matches_input'])
        self.assertFalse(cache['gdn']['conv']['plan_matches'])
        self.assertFalse(cache['gdn']['expected_ssm']['changed'])

    def test_budget_failure_preserves_msprobe_prediction_and_releases_request(self):
        result = self.operation(self.collect(budget=1), expected=1)
        self.assertEqual(result['cache_status'], 'incomplete')
        row = self.read(result['receipt'])
        self.assertEqual(row['token_id'], 17)
        self.assertTrue(row['decode_cache']['errors'])
        self.operation({'operation': 'end', 'source': self.session})

    def check_invalid_record(self, field, value):
        seal = self.collect(edit_record=lambda cache: cache.update({field: value}))
        result = self.operation(seal, expected=1)
        self.assertEqual(result['cache_status'], 'incomplete')
        self.assertEqual(self.read(result['receipt'])['token_id'], 17)

    def test_invalid_forward_is_incomplete(self):
        self.check_invalid_record('forward_id', 'other')

    def test_incomplete_operator_sequence_is_rejected(self):
        self.check_invalid_record('ops', [])

    def test_unavailable_engine_only_blocks_requested_cache(self):
        self.configure()
        engine = self.runtime / 'engine.json'
        ready = json.loads(engine.read_text())
        ready['decode_cache'] = {'status': 'unavailable', 'reason': 'fixture'}
        write(engine, ready)
        self.operation({'operation': 'arm', 'source': self.session, 'sample_key': 'q', 'native_id': 37,
                        'trace_id': 'denied', 'cache_output_index': 1}, expected=1)
        _, seal = self.invoke()
        self.assertNotIn('decode_cache', self.read(self.operation(seal)['receipt']))

    def test_missing_target_token_does_not_invent_cache(self):
        self.configure()
        self.tokens = [16]
        _, seal = self.invoke(cache_output_index=1)
        result = self.operation(seal, expected=1)
        self.assertEqual(result['token_count'], 1)
        self.assertEqual(result['cache_status'], 'incomplete')
