from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture import native_fixture as native
from worker_capture.session_fixture import write
from worker_capture.parallel_cases.test_identity import VERIFY
from verification.evidence import read_bound
from verification.runtime.capture.adapters.parallel import share
from verification.runtime.capture.parallel.receipt import collect
from verification.runtime.capture.parallel.reader import rank_view
from verification.runtime.capture.parallel.snapshots import isolate_files
from verification.runtime.capture.tensors import file_ref


class RankEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.folder = self.root / 'request-A'
        self.folder.mkdir()
        self.intent = {'request_id': 'A'}
        self.mapped = {'internal_id': 'req', 'prompt_ids': [7, 7, 7]}
        self.members = [dict(rank=i, state_shards={}, layers=['model.layers.0', 'model.layers.1'],
                             architecture={'layers': []}, vocab_size=100) for i in range(2)]
        for i, member in enumerate(self.members):
            write(self.root / f'worker-rank{i}.json', member)
        self.tokens = [7, 7]
        self.ids = []
        for n, (pos, count, ix) in enumerate([(0,2,None), (2,1,0), (3,1,1)]):
            self.add_frame(n, pos, count, ix)

    def add_frame(self, step, pos, count, ix):
        fid = uuid4().hex
        self.ids.append(fid)
        identity = dict(internal_id='req', request_id='A', ordinal=step, position=pos,
                        count=count, phase='prefill' if pos<3 else 'decode', output_index=ix or 0,
                        schedule_id=uuid4().hex, forward_id=fid)
        write(self.folder / f'dispatch-{step}.json', identity)
        folder, calls = native.native_dump(self.root, step)
        shutil.copytree(folder, folder.parent/'rank1')
        for rank in range(2):
            dest = self.folder / f'rank{rank}'
            frame = {**identity, 'dispatch': identity, 'rank': rank, 'positions': list(range(pos,pos+count)),
                     'input_ids': [7]*count, 'logits_indices':[count-1], 'step': step, 'probe_window_seconds':1,
                     'dump': str((folder.parent/f'rank{rank}').relative_to(self.root)),
                     'coverage': {'calls':{'input':calls, 'output':calls},
                                  'modules':{'begin':native.MODULES,'end':native.MODULES},
                                  'scope':'all_invoked_modules_and_msprobe_registered_apis'}}
            write(dest/f'begin-{fid}.json',frame)
            write(dest/f'end-{fid}.json',{**frame,'status':'completed'})
            if rank==0 and ix is not None:
                import struct
                vector = [0.0]*100
                vector[7] = 1
                for key in ['raw','scores']:
                    name = fid+'-'+key+'.bin'
                    (dest/name).write_bytes(struct.pack('<100f',*vector))
                    frame[key] = {'file':name, 'size':100, 'dtype':'torch.float32'}
                frame['candidate']=7
                write(dest/f'{fid}.json',frame)
                write(dest/f'prediction-{ix}.json',frame)
                write(self.folder/f'accepted-{ix}.json',dict(internal_id='req',forward_id=fid,output_index=ix,token_id=7))

    def collect(self):
        return collect(self.root,self.folder,self.intent,{'rank_workers':self.members},self.mapped,self.tokens)

    def test_each_forward_and_rank_with_repeated_token_ids(self):
        rows,errors=self.collect()
        self.assertEqual(errors,[])
        self.assertEqual([r['output_index'] for r in rows],[None,0,1])
        for n,row in enumerate(rows):
            for rank,local in enumerate(row['ranks']):
                data=read_bound(local['row'])
                self.assertEqual((data['forward_id'],data['rank'],data['step']),(self.ids[n],rank,n))
                self.assertEqual('raw' in data, rank==0 and n>0)

    def test_missing_rank_and_wrong_position_are_independent(self):
        (self.folder/'rank1'/f'begin-{self.ids[0]}.json').unlink()
        p=self.folder/'rank0'/f'end-{self.ids[2]}.json'
        write(p,{**json.loads(p.read_text()),'positions':[2]})
        rows,errors=self.collect()
        self.assertEqual(len(errors),2)
        self.assertEqual([r['status'] for r in rows[0]['ranks']], ['complete','incomplete'])
        self.assertEqual([r['status'] for r in rows[2]['ranks']], ['incomplete','complete'])

    def test_rejects_cross_rank_dump_and_tamper(self):
        p=self.folder/'rank1'/f'end-{self.ids[0]}.json'
        data=json.loads(p.read_text())
        write(p,{**data,'dump':data['dump'].replace('rank1','rank0')})
        rows,errors=self.collect()
        self.assertTrue(errors)
        self.assertEqual(rows[0]['ranks'][0]['status'],'complete')
        result={'workers':[file_ref(self.root/f'worker-rank{i}.json', self.root) for i in range(2)],
                'output_rank':0,'request_id':'A'}
        row=rows[1]
        path=Path(read_bound(row['ranks'][1]['row'])['calls'][0]['tensors']['output'][0]['path'])
        path.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'摘要'):
            rank_view(result,row,row['ranks'][1],{},self.folder)
        self.assertEqual(rank_view(result,row,row['ranks'][0],{},self.folder)['status'],'complete')
        from verification.runtime.capture.parallel.reader import validate_native_selection
        local=read_bound(row['ranks'][1]['row'])
        detail={**local, 'cache_snapshot':{'values':[7.0]}}
        errors=validate_native_selection(local,detail,row['forward_id'],path.parent.parent)
        self.assertIn('摘要',errors[0])
        self.assertEqual(detail['cache_snapshot']['values'],[7.0])
        self.assertEqual((detail['calls'],detail['native_metadata']),([],[]))

    def test_split_budget_and_single_missing_snapshot(self):
        self.assertEqual([share(SimpleNamespace(rank=i,world_size=2),5) for i in (0,1)], [3,2])
        files={}
        directory=self.root/'layer-3/call-0/state-0'
        directory.mkdir(parents=True)
        for name in ('key.after','value.after'):
            p=directory/(name+'.bin')
            p.write_bytes(b'12')
            files[name]={**file_ref(p,directory),'file':p.name,'shape':[1],'dtype':'torch.bfloat16','bytes':2}
        cache={'errors':[],'layers':[{'index':3,'path':'layers.3','errors':[],
               'invocations':[{'occurrence':0,'boundary':{},'states':[{'snapshots':files}]}]}]}
        (directory/'key.after.bin').unlink()
        self.assertIn('layers.3 key.after',isolate_files(self.root,cache)[0])
        self.assertEqual(list(files),['value.after'])

    def test_public_seal_lookup_and_missing_member(self):
        from verification.runtime.capture import action
        from verification.runtime.capture.parallel.receipt import seal_parallel
        from verification.evidence import write_json
        def ref(name, data, directory=None):
            directory = self.folder if directory is None else directory
            path = directory/name
            write_json(path, data)
            return file_ref(path, directory)
        source = ref('deployment.json', {})
        topo = ref('parallel.json', {'world_size':2}, self.root)
        prep = ref('prepare.json', {'parallel':topo}, self.root)
        config = ref('native-config.json', {}, self.root)
        ref('executor.json', {'output_rank':0}, self.root)
        ref('engine.json', {}, self.root)
        ref('request.json', {**self.mapped, 'external_id':'external'})
        ref('frontend.json', {'external_id':'external','trace_id':'A','response_id':'http-id'})
        ref('frontend-end.json', {'trace_id':'A','request_wall_seconds':1.5})
        ref('engine-output.json', {'external_id':'external','prompt_ids':self.mapped['prompt_ids'],
                                  'finish_reason':'length','token_ids':self.tokens})
        http={'request':ref('http-input.json', {'prompt':self.mapped['prompt_ids']}),
              'response':ref('http-output.json', {'id':'http-id','choices':[{'token_ids':self.tokens}]})}
        (self.folder/'rank1'/f'begin-{self.ids[0]}.json').unlink()
        sealed=seal_parallel({'trace_id':'A','http':http}, {'source':source,'derived_config':config},
            {'worker_capture':{'receipt':prep}}, self.root, self.folder,
            {**self.intent,'sample_key':'test','native_id':0}, {'rank_workers':self.members,'output_rank':0})
        self.assertEqual(sealed['status'],'incomplete')
        def lookup(**selector):
            spec={'source':sealed['receipt'],'request_id':'A',**selector}
            action.validate(spec, 'capture-read')
            return action.lookup(spec,self.root,'read')[1]
        self.assertEqual(len(lookup(view='timeline')['timeline']),3)
        both=lookup(prompt_position=0)
        self.assertEqual([r['status'] for r in both['ranks']], ['complete','incomplete'])
        row=lookup(forward_id=self.ids[1],rank=1,layer_index=0)
        self.assertEqual((row['status'],row['rank'],row['output_index']), ('complete',1,0))
        self.assertNotIn('scores',row)
        self.assertEqual(lookup(output_index=1,rank=0)['prefix_ids'],[7,7,7,7])

    def test_all_rank_caps_and_explicit_member_identity(self):
        from verification.runtime.capture.parallel.receipt import check_budgets
        errors=[]
        totals=[{'snapshot_bytes':5},{'snapshot_bytes':3}]
        ready={'rank_workers':[{'rank':i,'decode_cache':{'max_snapshot_bytes':3}} for i in (0,1)]}
        timeline=[{'forward_id':self.ids[0],'ranks':[{'snapshot_bytes':5,'errors':[]},{'snapshot_bytes':3,'errors':[]}]}]
        check_budgets({'cache_capture':{'max_request_snapshot_bytes':7}},ready,timeline,totals,errors)
        self.assertEqual(len(errors),2)
        self.assertTrue(all('rank0' in e for e in errors))
        self.assertEqual(timeline[0]['ranks'][0]['status'],'incomplete')
