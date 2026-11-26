"""公开回查验证落盘逆序、占位序号、丢弃及未完成租约。"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.batch_cases import test_public_batch as fixtures
from worker_capture.session_fixture import write


class AsyncReceipt(unittest.TestCase):
    def setUp(self):
        b = self.base = fixtures.BatchSession()
        b.prompts = {'A': [71], 'B': [71]}
        b.setUp()
        self.addCleanup(b.doCleanups)
        group = b.group / 'group.json'
        write(group, {**json.loads(group.read_bytes()), 'async_queue': {'queue_depth': 2}})

    def deliver(self, fid, index, overlaps, disposition):
        b = self.base
        path = b.group / f'dispatch-{index}.json'
        plan = json.loads(path.read_bytes())
        origin = [b.group.name, plan['schedule_id'], fid]
        for step in plan['requests'].values():
            step['output_index'] = 99  # 占位序号绝不能用于查询。
        write(path, plan)
        sample = json.loads((b.group / ('samples-' + fid + '.json')).read_bytes())
        ids = sorted(sample, key=lambda k: sample[k]['sample_row'])
        write(b.group / ('ready-' + fid + '.json'), dict(origin=origin, ready_ns=200-index,
              req_ids=ids, sample_rows={k:i for i,k in enumerate(ids)}, tokens=[sample[k]['token_ids'] for k in ids]))
        write(b.group / ('submit-' + fid + '.json'), dict(origin=origin,submitted_ns=100+index,
              queue_depth=2,previous_pending=overlaps))
        write(b.group / ('retired-' + fid + '.json'), dict(origin=origin,retired_ns=300+index,failed=False,
              dispositions={k:disposition for k in ids}, indices={k:index for k in ids} if disposition=='accepted' else {}))
        (b.group / ('inflight-' + fid + '.json')).unlink()

    def test_reverse_publish_and_seal_retry(self):
        b = self.base
        first = b.step(0,['B','A'],[0,0],[1,1],{'A':[71],'B':[71]},[0,0])
        second = b.step(1,['A','B'],[1,1],[1,1],{'A':[71],'B':[71]},[1,1])
        self.deliver(second,1,[first],'accepted')
        self.assertEqual(b.seal('A',[71,71])['status'],'pending')
        self.deliver(first,0,[],'accepted')
        for trace in ('B','A'):
            receipt = b.seal(trace,[71,71])['receipt']
            read = b.cli(dict(source=receipt,request_id=trace,output_index=1),'capture-read')
            self.assertEqual((read['forward_id'],read['async_events']['overlaps']),(second,[first]))
            self.assertEqual(read['batch_row'],int(trace=='B'))

    def test_peer_with_earlier_pending_result_does_not_block_member_seal(self):
        b = self.base
        first = b.step(0, ['A'], [0], [1], {'A':[71]}, [0])
        second = b.step(1, ['B'], [0], [1], {'B':[71]}, [0])
        prior = json.loads((b.group / 'dispatch-0.json').read_bytes())
        write(b.group / ('submit-' + first + '.json'),
              {'origin':[b.group.name, prior['schedule_id'], first], 'submitted_ns':100})
        self.deliver(second, 1, [first], 'accepted')
        path = b.group / ('retired-' + second + '.json')
        write(path, {**json.loads(path.read_bytes()), 'indices':{'B':0}})
        record = b.seal('B', [71])
        row = b.cli(dict(source=record['receipt'], request_id='B', output_index=0), 'capture-read')
        self.assertEqual(row['forward_id'], second)
        self.assertEqual(row['async_events']['overlap_unconfirmed'], [first])
        self.assertEqual(row['async_events']['overlaps'], [])
        self.deliver(first, 0, [], 'accepted')
        self.assertEqual(b.seal('A', [71])['status'], 'completed')

    def test_engine_dead_notification_reports_failed_instead_of_pending(self):
        from verification.runtime.capture.adapters.async_batch import terminal
        from verification.runtime.capture.tensors import file_ref
        from types import SimpleNamespace
        import os
        from unittest.mock import Mock
        b = self.base
        engine = b.runtime / 'engine.json'
        write(engine, {'pid':os.getpid(), 'namespace':'mock-ns','start_ticks':'1'})
        group = b.group / 'group.json'
        write(group, {**json.loads(group.read_bytes()),'engine_at_arm':file_ref(engine, b.runtime)})
        fid = b.step(0, ['A'], [0], [1], {'A':[71]}, [0])
        (b.group/'member-A'/'accepted-0.json').unlink()
        core = SimpleNamespace(_send_engine_dead=Mock())
        notify = core._send_engine_dead
        terminal.observe(core)
        core._send_engine_dead()
        notify.assert_called_once()
        folder = b.group / 'member-A'
        write(folder / 'frontend-end.json', {'trace_id':'A'})
        req = write(b.root / 'A-request', {'temperature':0})
        resp = write(b.root / 'A-error', {'error':{'message':'engine terminated'}})
        write(folder / 'frontend-payload-digest.json', {'http_sha256':req['sha256']})
        result = b.cli(dict(operation='seal',source=b.session,trace_id='A',
                            http={'request':req,'response':resp}), code=1)
        self.assertEqual(result['status'], 'failed')
        data = b.cli(dict(source=result['receipt'],request_id='A',view='timeline'), 'capture-read')
        self.assertEqual(data['inference_status'], 'failed')
        self.assertEqual(data['timeline'][0]['forward_id'], fid)
        write(b.group/'member-B'/'frontend-end.json', {'trace_id':'B'})
        b.cli(dict(operation='end',source=b.session))

    def test_abort_binds_empty_http_to_prior_accepted_tokens(self):
        import hashlib
        b = self.base
        fid = b.step(0, ['A','B'], [0,0], [1,1], {'A':[71],'B':[71]}, [0,0])
        self.deliver(fid, 0, [], 'accepted')
        member = b.group / 'member-A'
        write(member / 'frontend-end.json', {'trace_id':'A'})
        write(member / 'engine-abort.json', {'internal_id':'A', 'cancelled_ns':301})
        req = write(b.root / 'request.raw', {'temperature':0})
        write(member / 'frontend-payload-digest.json', {'http_sha256':req['sha256']})
        response = b.root / 'response.raw'
        response.write_bytes(b'')
        http = {'request':req, 'response':{'path':str(response), 'sha256':hashlib.sha256(b'').hexdigest()}}
        result = b.cli(dict(operation='seal', source=b.session, trace_id='A', http=http))
        row = b.cli(dict(source=result['receipt'], request_id='A', output_index=0), 'capture-read')
        self.assertEqual((row['delivery'],row['forward_id'],row['token_id']), ('cancelled',fid,71))

    def test_late_discarded_candidate_remains_queryable(self):
        b = self.base
        first = b.step(0,['A','B'],[0,0],[1,1],{'A':[71],'B':[71]},[0,0])
        late = b.step(1,['A','B'],[1,1],[1,1],{'A':[71],'B':[71]},[1,1])
        self.deliver(first,0,[],'accepted')
        self.deliver(late,1,[first],'finished_or_aborted')
        for key in ('A','B'):
            (b.group / ('member-'+key) / 'accepted-1.json').unlink()
        receipt=b.seal('A',[71])['receipt']
        read=b.cli(dict(source=receipt,request_id='A',forward_id=late),'capture-read')
        self.assertIsNone(read['output_index'])
        self.assertEqual(read['async_events']['disposition'],'finished_or_aborted')
