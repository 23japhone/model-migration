"""真实 CLI、独立 worker 进程和合成模型输出的闭环。"""
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.session_fixture import AGENT, BUNDLE, deployment, isolated_bundle, public, write
sys.path.insert(0, str(BUNDLE / AGENT / 'scripts'))
from verification.runtime.capture.adapters.batch import members, scheduler, worker


def execute(schedule, inputs):
    ids, tokens, positions, ends = inputs
    runner = NS(input_batch=NS(req_ids=ids), query_start_loc=NS(np=np.array([0, *ends])),
                logits_indices=np.array(ends) - 1)
    with worker.forward(schedule._verify_batch, runner, np.array(tokens), np.array(positions)):
        pass


class BatchSession(unittest.TestCase):
    prompts = {'A': [2, 3, 4, 5, 6], 'B': [8, 7, 6, 5, 4, 71, 9, 12]}

    def cli(self, request, action='capture-session', code=0):
        result, value = public(self.verify, self.root, request, action)
        self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
        return value

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.verify = isolated_bundle(self.root)
        report, self.runtime = deployment(self.root, 'http://127.0.0.1:1')
        self.enterContext(patch.dict(os.environ, VERIFY_EAGER_ROOT=str(self.runtime)))
        worker_file = self.runtime / 'worker.json'
        write(worker_file, {**json.loads(worker_file.read_bytes()), 'batch_identity': True, 'batch_joint': True})
        self.session = self.cli(dict(operation='begin', source=report, owner_pid=os.getpid()))['session']
        request = dict(operation='arm', source=self.session, profile=getattr(self, 'profile', 'batch_identity'),
                       requests=[dict(trace_id=k, sample_key=k, native_id=k) for k in self.prompts])
        self.group = self.runtime / self.cli(request)['group_id']
        for key, ids in self.prompts.items():
            folder = members.frontend_folder(key)
            external = 'external-' + key
            members.bind(self.group, 'external', external, key)
            req = NS(request_id=key, external_req_id=external)
            self.assertEqual(members.engine_folder(req), folder)
            write(folder / 'frontend.json', dict(trace_id=key, external_id=external, response_id=external))
            write(folder / 'request.json', dict(internal_id=key, external_id=external, prompt_ids=ids))

    def step(self, ordinal, layout, starts, counts, predictions, previous, omit=False, foreign=False):
        schedule = NS(num_scheduled_tokens=dict(zip(layout, counts)),
                      scheduled_new_reqs=[NS(req_id=k, num_computed_tokens=0) for k, s in zip(layout, starts) if s == 0],
                      scheduled_cached_reqs=NS(req_ids=layout, num_computed_tokens=starts, num_output_tokens=previous))
        if foreign:
            schedule.num_scheduled_tokens['OUTSIDE'] = 1
        scheduler.dispatch(self.group, schedule, ordinal)
        plan = schedule._verify_batch
        if foreign:
            self.assertNotIn('OUTSIDE', str(plan))
            return plan['forward_id']
        ends = np.cumsum(counts).tolist()
        tokens, positions = [], []
        for k, pos, count in zip(layout, starts, counts):
            tokens.extend((self.prompts[k] + [71, 71])[pos:pos+count])
            positions.extend(range(pos, pos+count))
        if not omit:
            child = multiprocessing.get_context('spawn').Process(target=execute, args=(schedule, (layout, tokens, positions, ends)))
            child.start()
            child.join(15)
            self.assertEqual(child.exitcode, 0)
        sample_order = layout[::-1]
        output = NS(req_ids=sample_order, req_id_to_index={k:i for i,k in enumerate(sample_order)},
                    sampled_token_ids=[predictions[k] for k in sample_order])
        scheduler.record_samples(plan, output)
        for k, index in zip(layout, previous):
            scheduler.accepted(plan, NS(request_id=k, num_output_tokens=index + 1), (predictions[k], False))
        return plan['forward_id']

    def seal(self, key, tokens, code=0):
        folder = self.group / ('member-' + key)
        external = 'external-' + key
        write(folder / 'frontend-end.json', dict(trace_id=key))
        write(folder / 'engine-output.json', dict(external_id=external, token_ids=tokens, finished=True,
                                                  prompt_ids=self.prompts[key], finish_reason='length'))
        req = write(self.root / (key + '-body'), dict(temperature=0))
        write(folder / 'frontend-payload-digest.json', dict(http_sha256=req['sha256']))
        resp = write(self.root / (key + '-response'), dict(id=external, choices=[dict(token_ids=tokens, finish_reason='length')]))
        return self.cli(dict(operation='seal', source=self.session, trace_id=key, http=dict(request=req,response=resp)), code=code)

    def test_mixed_batch_row_reuse_and_independent_seal(self):
        self.step(0, ['B','A'], [0,0], [5,5], {'A':[71], 'B':[]}, [0,0])
        fid = self.step(1, ['A','B'], [5,5], [1,2], {'A':[71], 'B':[]}, [1,0])
        a = self.seal('A', [71,71])['receipt']
        self.assertTrue((self.runtime/'active.json').exists())
        self.step(2, ['B'], [7], [1], {'B':[10]}, [0])
        b = self.seal('B', [10])['receipt']
        self.assertFalse((self.runtime/'active.json').exists())
        def read(ref, key, **selector):
            return self.cli(dict(source=ref, request_id=key, **selector), 'capture-read')
        a_row = read(a,'A',output_index=1)
        b_row = read(b,'B',prompt_position=5)
        self.assertEqual((a_row['forward_id'], b_row['forward_id']), (fid, fid))
        self.assertEqual((a_row['input_ids'], b_row['input_ids']), ([71], [71,9]))
        self.assertEqual((b_row['batch_row'], b_row['sample_row'], b_row['output_index']), (1,0,None))
        self.assertEqual(read(b,'B',output_index=0)['phase'], 'prefill')
        self.assertEqual(read(b,'B',output_index=0)['batch_row'], 0)
        self.assertEqual(read(a,'A',view='timeline')['timeline'][1]['forward_id'], fid)
        self.cli(dict(operation='end',source=self.session))
        write(self.group / ('worker-' + fid + '.json'), {})
        res, _ = public(self.verify,self.root,dict(source=a,request_id='A',forward_id=fid),'capture-read')
        self.assertNotEqual(res.returncode,0)

    def test_missing_execution_is_an_identified_gap(self):
        fid = self.step(0,['A'],[0],[5],{'A':[71]},[0],omit=True)
        sealed = self.seal('A',[71],code=1)
        self.assertIn(fid, str(sealed['errors']))
        self.assertEqual(sealed['status'],'incomplete')

    def test_inflight_group_and_duplicate_admission(self):
        self.assertIsNone(members.frontend_folder('not-enrolled'))
        self.assertIsNone(members.frontend_folder('A'))
        res, _ = public(self.verify,self.root,dict(operation='end', source=self.session))
        self.assertNotEqual(res.returncode,0)
        self.assertTrue((self.runtime/'active.json').exists())

    def test_swapped_sample_rows_with_identical_tokens_fail(self):
        plan = dict(group_id=self.group.name, forward_id='c'*32, requests={'A':{}, 'B':{}})
        output = NS(req_ids=['A','B'], req_id_to_index={'B':0,'A':1}, sampled_token_ids=[[71],[71]])
        with self.assertRaisesRegex(ValueError, '采样结果行'):
            scheduler.record_samples(plan,output)

    def test_unregistered_request_skips_inputs(self):
        fid = self.step(0,['B'],[0],[5],{},[0],foreign=True)
        sealed = self.seal('B',[],code=1)
        self.assertIn('未登记', str(sealed['errors']))
        self.assertFalse((self.group / ('worker-' + fid + '.json')).exists())
