"""用公开 CLI 封存热请求，复用区间和真实输出各自可查。"""
import json
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture import native_fixture as native, test_runtime_closure as closure
from worker_capture.session_fixture import write


class WarmTimeline(TestCase):
    setUp = closure.RuntimeClosure.setUp
    start = closure.RuntimeClosure.start
    invoke = closure.RuntimeClosure.invoke
    operation = closure.RuntimeClosure.operation

    def test_metadata_only_keeps_suffix_and_repeated_outputs(self):
        self.start(cache=True)
        self.tokens = [71, 71]
        path = self.runtime/'worker.json'
        worker = json.loads(path.read_bytes())
        worker['decode_cache'].update(full_request=True, prefix_reuse=True)
        worker['architecture'] = {'layers': [{'path': p, 'kind': 'stateless', 'states': []} for p in worker['layers']]}
        write(path, worker)
        original = native.respond
        self.enterContext(patch.object(native, 'respond', lambda root, trace, ids: original(root, trace, ids, forward=self.frame)))
        _, request = self.invoke(cache_capture={'mode':'full_request', 'max_request_snapshot_bytes':1,
                                                'prefix_reuse':True, 'observe_values':False})
        receipt = self.operation(request)['receipt']
        common = dict(source=receipt,request_id='sample-37')
        timeline = self.operation(dict(common,view='timeline'),action='capture-read')
        self.assertEqual(timeline['prefix_reuse']['source_status'],'metadata_only')
        self.assertEqual(timeline['prefix_reuse']['hit_tokens'],2)
        self.assertEqual([x['position_range'] for x in timeline['timeline']],[[2,3],[3,4]])
        prefix = self.operation(dict(common,prompt_position=1),action='capture-read')
        self.assertEqual(prefix['kind'],'reused_prefix')
        self.assertNotIn('forward_id',prefix)
        first = self.operation(dict(common,prompt_position=2),action='capture-read')
        second = self.operation(dict(common,output_index=1),action='capture-read')
        self.assertNotEqual(first['forward_id'], second['forward_id'])
        self.assertEqual(second['input_ids'],[71])

    @staticmethod
    def frame(folder, frame):
        n = frame['output_index']
        meta = {'epoch':'process', 'allocation':[]}
        if n == 0:
            frame.update(positions=[2],input_ids=[33],logits_indices=[0])
            meta['lookup']={'length':2,'groups':[]}
        frame['probe_window_seconds'] = 0.1
        identity=uuid4().hex
        plan=dict(internal_id=frame['internal_id'], forward_id=frame['forward_id'], phase='decode' if n else 'prefill',
                  output_index=n,position=2+n,count=1,schedule_id=identity,request_id='sample-37',groups=[],prefix_cache=meta)
        write(folder/('schedule-'+identity+'.json'),plan)
        cache={k:frame[k] for k in ('positions','step','output_index','forward_id','internal_id')}
        frame['decode_cache']={**cache,'rank':0,'format':'schedule_only','status':'metadata_only','errors':[],
                               'schedule_id':identity,'schedule':plan}
