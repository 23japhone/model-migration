"""公开会话封存含无输出分块的冷前缀请求，独立核对每个选择器。"""
import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture import native_fixture as native, test_runtime_closure as closure
from worker_capture.session_fixture import write

sys.path.insert(0, str(closure.VERIFY.parent))
from verification.runtime.capture import action, session


class TimelineTests(unittest.TestCase):
    setUp = closure.RuntimeClosure.setUp
    start = closure.RuntimeClosure.start
    invoke = closure.RuntimeClosure.invoke
    operation = closure.RuntimeClosure.operation

    def query(self, ref, expected=0, **selection):
        return self.operation(dict(source=ref, request_id='sample-37', **selection), expected, action='capture-read')

    def capture(self, early=False, missing_plan=False):
        self.start(cache=True)
        self.tokens = [71] if early else [71, 71, 92]
        worker = json.loads((self.runtime/'worker.json').read_bytes())
        worker['decode_cache']['full_request'] = True
        worker['architecture'] = {'layers': [{'path': p, 'kind': 'stateless', 'states': []} for p in worker['layers']]}
        write(self.runtime/'worker.json', worker)
        # 该夹具模拟预算耗尽时所有前向仍产生真实的 msProbe 文件和索引。

        def populate(folder, frame):
            frame['probe_window_seconds'] = 0.1
            if frame['output_index'] == 0:
                extra = dict(frame, forward_id=uuid4().hex, positions=[0, 1], input_ids=[11, 22], step=0)
                dump, _ = native.native_dump(self.runtime, 0)
                extra['dump'] = str(dump.relative_to(self.runtime))
                self.record(folder, extra)
                if missing_plan:
                    del extra['decode_cache']
                write(folder / f"begin-{extra['forward_id']}.json", extra)
                write(folder / f"end-{extra['forward_id']}.json", dict(extra, status='completed'))
                frame.update(positions=[2], input_ids=[33], logits_indices=[0])
            self.record(folder, frame)

        original = native.respond
        self.enterContext(patch.object(native, 'respond', lambda root, trace, ids: original(root, trace, ids, forward=populate)))
        _, request = self.invoke(cache_capture=dict(mode='full_request', max_request_snapshot_bytes=1))
        sealed = self.operation(request, expected=1)
        self.assertIn('receipt', sealed, sealed)
        return sealed['receipt']

    @staticmethod
    def record(folder, frame):
        identity = uuid4().hex
        pos = frame['positions'][0]
        schedule = dict(internal_id=frame['internal_id'], output_index=frame['output_index'], position=pos,
                        count=len(frame['positions']), phase='prefill' if pos < 3 else 'decode',
                        request_id='sample-37', schedule_id=identity, groups=[])
        write(folder / f'schedule-{identity}.json', schedule)
        cache = {k: frame[k] for k in ('internal_id', 'output_index', 'positions', 'forward_id', 'step')}
        cache.update(rank=0, schedule_id=identity, schedule=schedule, status='incomplete', errors=['请求预算耗尽'])
        frame['decode_cache'] = cache

    def test_all_selectors_on_chunked_prefix_and_repeated_ids(self):
        receipt = self.capture()
        view = self.query(receipt, view='timeline')
        frames = view['timeline']
        self.assertEqual([f['output_index'] for f in frames], [None, 0, 1, 2])
        self.assertEqual([f['phase'] for f in frames], ['prefill', 'prefill', 'decode', 'decode'])
        self.assertGreater(view['capture_totals']['msprobe_bytes'], 0)
        self.assertEqual(view['cache_capture']['max_request_snapshot_bytes'], 1)
        expected_layers = json.loads((self.runtime/'worker.json').read_bytes())['layers']
        self.assertTrue(expected_layers)
        self.assertEqual(frames[0]['missing_layers'], expected_layers)
        no_output = self.query(receipt, forward_id=frames[0]['forward_id'])
        self.assertIsNone(no_output['output_index'])
        self.assertNotIn('scores', no_output)
        self.assertEqual(self.query(receipt, prompt_position=0)['forward_id'], no_output['forward_id'])
        for i in range(3):
            row = self.query(receipt, output_index=i)
            self.assertEqual(row['forward_id'], frames[i+1]['forward_id'])
            self.assertEqual(row['timeline_entry']['forward_id'], row['forward_id'])
        self.query(receipt, expected=1, prompt_position=3)
        tensor = no_output['calls'][0]['tensors']['output'][0]
        Path(tensor['path']).write_bytes(b'changed')
        self.query(receipt, expected=1, prompt_position=0)
        # 另一前向的独立原生文件仍能按输出序号读回。
        self.assertEqual(self.query(receipt, output_index=2)['forward_id'], frames[-1]['forward_id'])

    def test_missing_plan_keeps_prompt_positions_searchable(self):
        receipt = self.capture(early=True, missing_plan=True)
        first = self.query(receipt, view='timeline')['timeline'][0]
        self.assertEqual(first['phase'], 'prefill')
        self.assertEqual(first['phase_source'], 'worker_input_positions')
        self.assertEqual(first['cache_status'], 'incomplete')
        self.assertEqual(self.query(receipt, prompt_position=1)['forward_id'], first['forward_id'])

    def test_eos_preserves_only_real_outputs(self):
        receipt = self.capture(early=True)
        entries = self.query(receipt, view='timeline')['timeline']
        self.assertEqual([x['output_index'] for x in entries], [None, 0])
        self.query(receipt, expected=1, output_index=1)

    def test_scheduler_identifies_new_and_cached_prompt_chunks(self):
        from verification.runtime.capture.adapters.decode_cache import plan
        write(self.root / 'intent.json', dict(request_id='trace', cache_capture={'mode': 'full_request'}))
        write(self.root / 'request.json', dict(prompt_ids=[11, 22, 33]))
        group = NS(layer_names=['model.layers.0'], kv_cache_spec=NS(block_size=8))
        scheduler = NS(kv_cache_manager=NS(get_block_ids=lambda _: [[5]]), kv_cache_config=NS(kv_cache_groups=[group]))
        new = NS(req_id='r', num_computed_tokens=0)
        cached = NS(req_ids=['r'], num_output_tokens=[0], num_computed_tokens=[2])
        output = NS(scheduled_new_reqs=[new], scheduled_cached_reqs=cached, num_scheduled_tokens={'r': 2})
        first = plan.request_plan(scheduler, output, 'r', self.root)
        self.assertEqual((first['phase'], first['count'], first['position']), ('prefill', 2, 0))
        new.num_computed_tokens = 2
        with self.assertRaisesRegex(ValueError, '冷前缀'):
            plan.request_plan(scheduler, output, 'r', self.root)
        output.scheduled_new_reqs = []
        output.num_scheduled_tokens['r'] = 1
        last = plan.request_plan(scheduler, output, 'r', self.root)
        self.assertEqual((last['phase'], last['count'], last['position']), ('prefill', 1, 2))
        cached.num_computed_tokens[0] = 3
        cached.num_output_tokens[0] = 1
        self.assertEqual(plan.request_plan(scheduler, output, 'r', self.root)['phase'], 'decode')

    def test_mutually_exclusive_requests(self):
        ref = {'path': '/a', 'sha256': '0'*64}
        req = dict(source=ref, request_id='r', view='timeline')
        with self.assertRaises(ValueError):
            action.validate(dict(req, output_index=0), 'capture-read')
        arm = dict(source=ref, operation='arm', sample_key='a', native_id=1, trace_id='r',
                   cache_capture=dict(mode='full_request', max_request_snapshot_bytes=8))
        session.validate(arm)
        with self.assertRaises(ValueError):
            session.validate(dict(arm, cache_output_index=1))
