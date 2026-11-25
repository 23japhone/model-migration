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


def receive(connection, rank, folder):
    from verification.runtime.capture.adapters.parallel import select_frame
    with connection:
        schedule = connection.recv()
        rec = SimpleNamespace(rank=rank, folder=folder)
        frame = select_frame(rec, schedule, 'req')
        connection.send((os.getpid(), frame, rec.folder))


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
            install_schedule(Scheduler, parallel=True)
            context = multiprocessing.get_context('spawn')
            for size in (2, 4, 8):
                with self.subTest(size=size):
                    state.write(root / 'parallel.json', {'world_size': size})
                    folder = root / f'request-{size}'
                    folder.mkdir()
                    state.write(root / 'active.json', {'directory': folder.name})
                    state.write(folder / 'request.json', {'internal_id': 'req', 'prompt_ids': [7, 7, 8]})
                    state.write(folder / 'intent.json', {'request_id': 'trace'})
                    schedule = Scheduler().schedule()
                    identity = schedule._verify_forward
                    self.assertEqual((identity['ordinal'], identity['position'], identity['count']), (0, 0, 2))
                    self.assertEqual(identity['request_id'], 'trace')
                    workers = []
                    try:
                        for rank in range(size):
                            parent, child = context.Pipe()
                            worker = context.Process(target=receive, args=(child, rank, folder))
                            worker.start()
                            child.close()
                            workers.append((parent, worker))
                        for parent, _ in workers:
                            parent.send(schedule)
                        pids = set()
                        for rank, (parent, worker) in enumerate(workers):
                            self.assertTrue(parent.poll(15))
                            pid, frame, path = parent.recv()
                            pids.add(pid)
                            self.assertEqual(pid, worker.pid)
                            self.assertEqual((frame['rank'], frame['dispatch']), (rank, identity))
                            self.assertEqual(frame['forward_id'], identity['forward_id'])
                            self.assertEqual(path, folder / f'rank{rank}')
                            self.assertTrue(path.is_dir())
                        self.assertEqual(len(pids), size)
                    finally:
                        for parent, worker in workers:
                            parent.close()
                            worker.join(15)
                            if worker.is_alive():
                                worker.kill()
                                worker.join()
                    self.assertTrue(all(w.exitcode == 0 for _, w in workers))

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
        for rank in (True, -1, 2.0):
            with self.assertRaisesRegex(ValueError,'rank'):
                action.validate_forward_selector({'rank':rank})
        for rank in (2, 3, 7):
            action.validate_forward_selector({'rank': rank})

    def test_scheduler_snapshot_cannot_be_rebound_to_another_dispatch(self):
        from verification.runtime.capture.cache_evidence import check_identity
        with self.assertRaisesRegex(ValueError,'调度身份'):
            check_identity({'schedule_id':'a'*32}, {'dispatch':{'schedule_id':'b'*32}})
