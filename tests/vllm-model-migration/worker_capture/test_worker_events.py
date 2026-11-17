"""走适配器的实际调用顺序，覆盖分段 prefill、无输出前向和原地选词。"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))
from verification.runtime.capture.adapters import engine, state, worker


class Inputs:
    @staticmethod
    def assign_request_id(request):
        request.external_req_id = request.request_id
        request.request_id += '-engine-suffix'


class Scheduled:
    def __init__(self):
        pass

    def _update_request_with_output(self, request, ids):
        request.num_output_tokens += len(ids)
        return ids, request.num_output_tokens == 2


class Result:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class Runner:
    def load_model(self):
        pass

    def execute_model(self, schedule):
        self._model_forward(1, self.input, [self.position])
        self.scope_log.append('compute_logits') if hasattr(self, 'scope_log') else None
        self.execute_model_state = NS(logits=[[0., 5., 2.]])

    def _model_forward(self, count, ids, positions):
        pass

    def sample_tokens(self):
        scores = self.execute_model_state.logits
        scores[0][1] = -10.
        self.sampler.greedy_sample(scores)
        output = [] if self.position == 0 else [2]
        return NS(sampled_token_ids=[output], req_id_to_index={self.internal: 0})


class Events(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.folder = self.root / 'one'
        self.folder.mkdir()
        self.enterContext(patch.dict(os.environ, VERIFY_EAGER_ROOT=str(self.root)))
        state.write(self.root / 'active.json', {'directory': 'one'})
        state.write(self.folder / 'intent.json', {'request_id': 'client'})
        # 用独立的目标类安装实际适配器，避免改写其他测试用例。
        self.input = type('Input', (Inputs,), {})
        self.scheduler = type('Scheduler', (Scheduled,), {})
        self.runner = type('NPU', (Runner,), {})
        self.enterContext(patch.object(state, 'ready'))
        engine.input_processor(self.input)
        engine.scheduler(self.scheduler)
        worker.install(self.runner)

    def test_chunks_repeated_tokens_and_inplace_scores(self):
        req = NS(request_id='cmpl-client-0', prompt_token_ids=[11,22], num_output_tokens=0,
                 get_finished_reason=lambda: 'length')
        self.input.assign_request_id(req)
        schedule = NS(num_scheduled_tokens={req.request_id: 1})
        runner = self.runner()
        runner.internal = req.request_id
        runner.sampler = NS(greedy_sample=lambda scores: Result(scores[0].index(max(scores[0]))))
        runner.input_batch = NS(sampling_metadata=NS(all_greedy=True))
        rec = object.__new__(worker.Recorder)
        rec.runner, rec.size, rec.debugger = runner, 3, None
        calls = []
        def start(ids, positions):
            rec.frame.update(input_ids=ids, positions=positions, logits_indices=[0],
                             output_index=req.num_output_tokens, batch_row=0)
            calls.append('start')
        rec.start, rec.close = start, lambda: calls.append('close')
        rec.install_sampler()
        runner._verify_recorder = rec
        scheduler = self.scheduler()
        def save(path, scores, size):
            path.write_text(json.dumps(scores[0]))
            return {'file': path.name, 'size': size}
        with patch.object(worker, 'save_vector', side_effect=save):
            for pos, token in enumerate([11,22,2]):
                runner.position, runner.input = pos, [token]
                runner.execute_model(schedule)
                result = runner.sample_tokens()
                scheduler._update_request_with_output(req, result.sampled_token_ids[0])
        self.assertEqual(calls, ['start','close'] * 3)
        self.assertFalse(list(self.folder.glob('error-*')))
        first, second = [state.read(self.folder / f'accepted-{i}.json') for i in range(2)]
        self.assertNotEqual(first['forward_id'], second['forward_id'])
        forward = state.read(self.folder / 'prediction-1.json')
        self.assertEqual(forward['input_ids'], [2])
        self.assertEqual(state.read(self.folder / forward['raw']['file']), [0.,5.,2.])
        self.assertEqual(state.read(self.folder / forward['scores']['file']), [0.,-10.,2.])
        self.assertEqual(state.read(self.folder / 'request.json')['internal_id'], req.request_id)

    def test_mixed_request_rejected(self):
        req = NS(request_id='cmpl-client-0', prompt_token_ids=[1])
        self.input.assign_request_id(req)
        rec = object.__new__(worker.Recorder)
        with self.assertRaisesRegex(ValueError, '混入'):
            rec.select(NS(num_scheduled_tokens={req.request_id: 1, 'other': 1}))

    def test_dump_scope_includes_compute_logits(self):
        state.write(self.folder/'request.json', {'internal_id':'internal'})
        runner = self.runner(); runner.input, runner.position = [1], 0
        runner.scope_log = []
        rec = object.__new__(worker.Recorder); rec.runner = runner
        rec.start = lambda *args: runner.scope_log.append('start')
        rec.close = lambda: runner.scope_log.append('stop')
        rec.logits = lambda: runner.scope_log.append('raw_copy')
        runner._verify_recorder = rec
        runner.execute_model(NS(num_scheduled_tokens={'internal':1}))
        self.assertEqual(runner.scope_log, ['start','compute_logits','stop','raw_copy'])
