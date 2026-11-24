from pathlib import Path
from types import SimpleNamespace
import multiprocessing
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

VERIFY = next(Path(__file__).resolve().parents[4].glob('skills/*/skills/*/skills/verify-agent'))
sys.path.insert(0, str(VERIFY / 'scripts'))
from verification.runtime.capture.contract import check_service
from verification.runtime.capture.adapters import state
from verification.runtime.capture.adapters.decode_cache.plan import install_schedule


def receive(connection):
    schedule = connection.recv()
    connection.send(schedule._verify_forward)
    connection.close()


class IdentityTests(unittest.TestCase):
    def test_double_card_contract(self):
        req = {'service': {'ownership': 'owned', 'command': ['vllm', 'serve', '--enforce-eager', '-tp=2']},
               'npu': {'devices': [6, 4]}}
        check_service(req)
        for flags in (['-pp=2'], ['-tp=1'], ['--distributed-executor-backend=ray'], ['--enable-expert-parallel']):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                check_service({**req, 'service': {**req['service'], 'command': req['service']['command'] + flags}})

        from verification.runtime.capture.adapters.parallel import check_parallel_dimensions
        with self.assertRaises(ValueError):
            check_parallel_dimensions(SimpleNamespace(enable_expert_parallel=True))

    def test_actual_multiprocess_transport_without_cache(self):
        output = SimpleNamespace(num_scheduled_tokens={'req': 2}, scheduled_cached_reqs=None,
                                 scheduled_new_reqs=[SimpleNamespace(req_id='req', num_computed_tokens=0)])
        class Scheduler:
            def schedule(self):
                return output
        with tempfile.TemporaryDirectory() as base, patch.dict(os.environ, VERIFY_EAGER_ROOT=base):
            root = Path(base)
            state.write(root / 'parallel.json', {'world_size': 2})
            state.write(root / 'active.json', {'directory': 'request'})
            folder = root / 'request'
            folder.mkdir()
            state.write(folder / 'request.json', {'internal_id': 'req', 'prompt_ids': [7, 7, 8]})
            state.write(folder / 'intent.json', {'request_id': 'trace'})
            install_schedule(Scheduler, parallel=True)
            schedule = Scheduler().schedule()
            identity = schedule._verify_forward
            context = multiprocessing.get_context('spawn')
            for rank in range(2):
                parent, child = context.Pipe()
                worker = context.Process(target=receive, args=(child,))
                worker.start()
                child.close()
                try:
                    parent.send(schedule)
                    self.assertTrue(parent.poll(15))
                    self.assertEqual(parent.recv(), identity)
                finally:
                    parent.close()
                    worker.join(15)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(identity['ordinal'], 0)
            self.assertEqual(identity['position'], 0)
            self.assertEqual(identity['count'], 2)
            self.assertEqual(identity['request_id'], 'trace')

    def test_runtime_member_mapping_and_lost_worker_after_arm(self):
        from verification.runtime.capture.parallel.topology import validate_members, bind_at_arm, seal_members
        from verification.runtime.capture.adapters.parallel import EXECUTOR_SHA
        devices=[dict(physical_id=6, visible_index=0), dict(physical_id=4,visible_index=1)]
        records={f'worker-rank{i}':{**d,'rank':i,'tp_rank':i,'world_size':2,'group_ranks':[0,1],
                 'layers':['stack.0'],'architecture':{},'pid':123+i,'namespace':'namespace'} for i,d in enumerate(devices)}
        records['executor']=dict(world_size=2,output_rank=0,sha256=EXECUTOR_SHA)
        ready=validate_members({'devices':devices},records)
        with tempfile.TemporaryDirectory() as base:
            folder=Path(base).resolve()
            refs=bind_at_arm(folder,ready)
            def gone():
                raise ValueError('worker-rank1 就绪文件缺失')
            at_seal=seal_members(folder,refs,gone)
            self.assertEqual([r['rank'] for r in at_seal['rank_workers']],[0,1])
            self.assertIn('worker-rank1',at_seal['service_errors'][0])
        records['worker-rank1']['physical_id']=6
        with self.assertRaisesRegex(ValueError,'映射'):
            validate_members({'devices':devices},records)

    def test_failure_receipt_and_rank_selector(self):
        from verification.runtime.capture import action
        from verification.runtime.capture.parallel.failure import inference_failed
        from verification.evidence import read_bound
        with tempfile.TemporaryDirectory() as base:
            folder=Path(base).resolve()
            state.write(folder/'intent.json',{'request_id':'A'})
            state.write(folder/'frontend-end.json',{'trace_id':'A'})
            result=inference_failed({'trace_id':'A','http':{}},folder,{'native_id':1,'sample_key':'prompt'},
                                    {'error':{'message':'engine stopped'}})
            self.assertEqual(read_bound(result['receipt'])['inference_status'],'failed')
        for rank in (True,-1,2):
            with self.assertRaisesRegex(ValueError,'rank'):
                action.validate_forward_selector({'rank':rank})

    def test_scheduler_snapshot_cannot_be_rebound_to_another_dispatch(self):
        from verification.runtime.capture.cache_evidence import check_identity
        with self.assertRaisesRegex(ValueError,'调度身份'):
            check_identity({'schedule_id':'a'*32}, {'dispatch':{'schedule_id':'b'*32}})
