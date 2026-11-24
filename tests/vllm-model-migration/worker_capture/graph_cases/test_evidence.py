"""图来源、实际槽位以及部分失败后的公开查询。"""
from pathlib import Path
import json
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/scripts'))
from verification.runtime.capture import action
from verification.runtime.capture.aclgraph import evidence
from verification.runtime.capture.tensors import file_ref


class GraphEvidenceTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()

    def record(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content))
        return file_ref(path, self.root)

    def test_no_output_block_and_repeated_output_with_a_missing_snapshot(self):
        raw = self.root / 'after.bin'
        raw.write_bytes(struct.pack('<f', 7.5))
        ref = dict(file_ref(raw, self.root), shape=[1], size=1, dtype='torch.float32')
        missing = {**ref, 'path': str(self.root / 'missing.bin')}
        rows = []
        for i, index in enumerate((None, 0, 1)):
            item = dict(forward_id=f'{i:032x}', schedule_id=f'{i+3:032x}', output_index=index,
                        input_ids=[42], phase='prefill' if i < 2 else 'decode', position_range=[i,i+1])
            row = {**item, 'replays': [], 'layers': [dict(path='model.layers.0',index=0,
                   files={'key.after':ref, 'value.after':missing})]}
            rows.append({**item, 'row':self.record('row'+str(i),row)})
        receipt = self.record('receipt',dict(mode='aclgraph',request_id='r', cache_errors=[], timeline=rows,
                              worker=self.record('worker', {'layers':['model.layers.0']}), http={}))
        def read(**params):
            query = dict(request_id='r', source=receipt, layer_index=0, cache_snapshot='key.after', **params)
            action.validate(query,'capture-read')
            return action.lookup(query,self.root,'read')[1]
        for params, i in (({'prompt_position':0},0),({'output_index':1},2)):
            result = read(**params)
            self.assertEqual(result['forward_id'],rows[i]['forward_id'])
            self.assertEqual(result['cache_snapshot']['values'],[7.5])
            self.assertEqual(result['cache_status'],'incomplete')
            self.assertIn('value.after',str(result['cache_errors']))
        raw.write_bytes(struct.pack('<f',4))
        result = read(output_index=0)
        self.assertNotIn('cache_snapshot',result)
        self.assertIn('摘要',str(result['cache_errors']))

    def test_source_must_belong_to_this_forward_and_replay(self):
        frame = {'forward_id':'a','replays':[dict(graph_id='b',ordinal=3)]}
        for src in [dict(kind='graph',graph_id='b',ordinal=2),
                    dict(kind='outside_graph',forward_id='c'), {}]:
            with self.assertRaises(ValueError):
                evidence.check_source(src, frame)
        evidence.check_source(dict(kind='graph',graph_id='b',ordinal=3),frame)
        evidence.check_source(dict(kind='outside_graph',forward_id='a'),frame)

    def test_cross_page_slots_and_padding(self):
        path = self.root/'slots'
        ref = dict(sha256='1',shape=[2,1],dtype='torch.float32')
        files = {k:ref for k in ('key.input','key.after','value.input','value.after')}
        files['write.slots'] = {'path':str(path), 'dtype':'torch.int32'}
        data = {'index':0,'files':files}
        args = (data,dict(count=2,position=3),dict(blocks=[2,5],page_size=4),
                dict(backend_reads={'0':dict(source='paged_kv',backend='FIA',kv_length=5,
                     query_length=2,blocks=[2,5],block_size=4,sparse_mode=3)}))
        path.write_bytes(struct.pack('<3i',11,20,-1))
        evidence.check_attention(*args)
        self.assertEqual(data['plan']['causal_end_exclusive'],[4,5])
        for slots in ((10,20,-1),(11,20,0)):
            path.write_bytes(struct.pack('<3i',*slots))
            with self.assertRaisesRegex(ValueError,'槽'):
                evidence.check_attention(*args)

    def test_gdn_cold_and_continued_chunk_check_actual_kernel_states(self):
        before = self.root / 'before'
        initial = self.root / 'initial'
        before.write_bytes(b'aa')
        initial.write_bytes(b'\0\0')
        flag = self.root / 'flag'
        flag.write_bytes(b'\0')
        files = {'state.initial':dict(path=str(flag),dtype='torch.bool'), 'ssm.initial':file_ref(initial,self.root),
                 'ssm.before':file_ref(before,self.root), 'ssm.after':{'sha256':'final'}, 'ssm.final':{'sha256':'final'}}
        layer = dict(plan={},files=files)
        evidence.check_chunk_initial(layer,{'position':0},{'initial_mode':[0]})
        self.assertEqual(layer['plan']['initial_source'],'zero')
        flag.write_bytes(b'\1')
        with self.assertRaisesRegex(ValueError,'写回'):
            evidence.check_chunk_initial(layer,{'position':2},{'initial_mode':[1]})
        files['ssm.initial'] = files['ssm.before']
        evidence.check_chunk_initial(layer,{'position':2},{'initial_mode':[1]})
        self.assertEqual(layer['plan']['initial_source'],'cache')

    def test_failed_inference_keeps_worker_end_records(self):
        from verification.runtime.capture import session_receipt
        intent = dict(request_id='r',sample_key='sample',native_id=1)
        self.record('intent.json',intent)
        self.record('frontend-end.json',{'trace_id':'r'})
        self.record('end-'+'a'*32+'.json',{'status':'failed'})
        req = dict(trace_id='r',http={'request': self.record('request.raw',{'model':'m'}),
                                      'response': self.record('response.raw',{'error':{'message':'engine stopped'}})})
        ready = {'mode':'aclgraph'}
        result = session_receipt.seal(req,{}, {},self.root,{'state':'armed','trace_id':'r','directory':'.'},ready)
        self.assertEqual(result['inference_status'],'failed')
        self.assertEqual(len(result['forwards']),1)
        query = dict(request_id='r', source=result['receipt'],view='timeline')
        self.assertEqual(action.lookup(query,self.root,'read')[1]['diagnostic'],{'message':'engine stopped'})
