"""两个实际 Future 逆序完成，每个结果保留自己的调度身份。"""
from concurrent.futures import Future
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py')).parent))
from verification.runtime.capture.adapters import state
from verification.runtime.capture.adapters.async_batch import engine, worker


class AsyncBatch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enterContext(patch.dict(os.environ, VERIFY_EAGER_ROOT=tmp.name))
        self.group = self.root / 'g'
        self.group.mkdir()

    def plan(self, fid):
        return dict(group_id='g', forward_id=fid, schedule_id='s'+fid, requests={'A':{}, 'B':{}}, asynchronous=True)

    def test_future_and_request_rows_survive_worker_reuse(self):
        bindings = worker.Pending()
        futures = []
        for fid, ids in [('first',['A','B']), ('second',['B','A'])]:
            plan = self.plan(fid)
            pending = object()
            bindings.bind(pending, plan)
            future = Future()
            wrapper = NS(get_output=future.result)
            worker.track_output(wrapper, bindings.take(pending))
            result = NS(req_ids=ids, req_id_to_index={k:i for i,k in enumerate(ids)}, sampled_token_ids=[[71],[71]])
            futures.append((future, wrapper, result, plan))
        for future, wrapper, result, plan in reversed(futures):
            future.set_result(result)
            observed = wrapper.get_output()
            engine.check_output(plan, observed)
            self.assertRaises(ValueError, engine.check_output, self.plan('other'), observed)
            record = state.read(self.group / ('ready-'+plan['forward_id']+'.json'))
            self.assertEqual(record['req_ids'],result.req_ids)

    def test_lazy_future_with_ready_worker_is_not_proof_of_overlap(self):
        from verification.runtime.capture.batch_identity.async_steps import overlap_ids
        state.write(self.group / 'ready-first.json', {'origin':['g','sfirst','first'], 'ready_ns':200})
        state.write(self.group / 'submit-first.json', {'origin':['g','sfirst','first'], 'submitted_ns':50})
        event = {'previous_pending':['first'], 'submitted_ns':201}
        self.assertEqual(overlap_ids(self.group, 'second', event, state.read), ([], []))

    def test_overlap_uses_unready_result_of_a_different_target_forward(self):
        prev, new = self.plan('first'), self.plan('second')
        future = Future()
        queue = [(future, NS(_verify_batch=prev), Future())]
        self.assertEqual(engine.outstanding(queue,new),['first'])
        future.set_result(None)
        self.assertEqual(engine.outstanding(queue,new),[])
        self.assertEqual(engine.outstanding([(Future(), NS(), Future())],new),[])
