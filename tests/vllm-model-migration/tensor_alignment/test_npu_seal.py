"""经公开 capture-session 封存指定前向、核对实物并保留不完整证据。"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS))
from worker_capture.session_fixture import deployment, isolated_bundle, public, write


class NpuTensorSession(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.cli = isolated_bundle(self.root)
        source, self.runtime = deployment(self.root, 'http://127.0.0.1:8888/v1')
        path = self.runtime / 'worker.json'
        write(path, {**json.loads(path.read_bytes()), 'mode':'eager','synchronous':True,
                     'token_prediction':True,'tensor_alignment':True,'vocab_size':3})
        self.session = self.call({'operation':'begin','source':source,'owner_pid':os.getpid()})['session']
        self.call_name = 'layer0'
        self.selector = {'id':'out', 'role':'output', 'npu':['output',0]}
        call = {'id':self.call_name, 'kind':'module', 'occurrence':0,
                'npu':{'owner':'model','name':'linear'},'slots':[self.selector]}
        self.plan = {'format':'tensor-plan-v1','case':{'subset':'set','sample_id':3},'position':2,
                     'model_identity':'1'*64,'runtime_versions':{'npu':{'torch':'t','vllm':'v','msprobe':'m'}},'prompt_token_ids':[1],'prefix_token_ids':[1,0],'calls':[call]}
        self.plan_ref = write(self.root / 'plan.json', self.plan)
        self.arm = {'operation':'arm','source':self.session,'trace_id':'q3','profile':'tensor_alignment',
                    'tensor_plan':self.plan_ref,'native_id':3,'sample_key':3}

    def call(self, request, code=0):
        proc, data = public(self.cli, self.root, request)
        self.assertEqual(proc.returncode, code, (data,proc.stderr))
        return data

    def run_fixture(self, produce=True):
        import torch
        folder = self.runtime / self.call(self.arm)['directory']
        self.assertEqual((folder / 'tensor-plan.json').read_bytes(), Path(self.plan_ref['path']).read_bytes())
        Path(self.plan_ref['path']).unlink()  # Worker and seal must use the captured bytes on the common mount.
        native = self.runtime / 'dump/native/step3/rank0'
        native.mkdir(parents=True)
        write(folder/'request.json', {'internal_id':'r','external_id':'ext','prompt_ids':[1]})
        write(folder/'frontend.json', {'trace_id':'q3','external_id':'ext','response_id':'resp'})
        write(folder/'frontend-end.json', {'trace_id':'q3'})
        write(folder/'engine-output.json', {'external_id':'ext','prompt_ids':[1],'finished':True,
                                           'token_ids':[0,2],'finish_reason':'stop'})
        fid = 'a' * 32
        rows = []
        if produce:
            path = folder/'target-0.pt'
            torch.save(torch.ones(2), path)
            rows.append({'call':self.call_name,'slot':'out','path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'shape':[2],'dtype':'float32','layout':'strided','ordinal':2,'occurrence':0,
                         'native_name':'Module.model.linear.Linear.forward.0','native_parent':'model','argument':['output',0]})
        frame = {'profile':'tensor_alignment','targeted':True,'forward_id':fid,'internal_id':'r','output_index':1,
                 'capture_seconds': 0.01, 'versions':self.plan['runtime_versions']['npu'],'positions':[1],'input_ids':[0],'logits_indices':[0],'candidate':2,'native_step':3,'tensors':rows,'tensor_errors':[]}
        for key, vals in [('raw',(5.,2.,0.)), ('processed_probs',(.1,.2,.7))]:
            path = folder/(fid+'-'+key+'.bin')
            path.write_bytes(struct.pack('<3f',*vals))
            frame[key] = {'file':path.name,'size':3,'dtype':'torch.float32'}
        for name in (fid, 'begin-'+fid, 'prediction-1', 'end-'+fid):
            write(folder/(name+'.json'), {**frame, **({'status':'completed'} if name.startswith('end-') else {})})
        write(folder/'target-frame.json', {'forward_id':fid,'plan_sha256':self.plan_ref['sha256']})
        write(folder/'accepted-1.json', {'forward_id':fid,'internal_id':'r','output_index':1,'token_id':2})
        req = write(folder/'req.json', {'temperature':0,'return_token_ids': True})
        write(folder/'frontend-payload-digest.json', {'http_sha256':req['sha256']})
        res = write(folder/'res.json', {'id':'resp','choices':[{'token_ids':[0,2],'finish_reason':'stop'}]})
        return folder, {'operation':'seal','source':self.session,'trace_id':'q3','http':{'request':req,'response':res}}

    def test_complete_and_bound_native_manifest(self):
        folder, seal = self.run_fixture()
        report = self.call(seal)
        self.assertEqual(report['status'], 'completed')
        bundle = json.loads(Path(report['tensor_bundle']['path']).read_bytes())
        ref = bundle['native_index']
        data = (folder/ref['path']).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),ref['sha256'])
        native = json.loads(data)
        self.assertEqual(native['tensors'],bundle['tensors'])
        self.assertEqual(native['execution']['output_index'],1)
        self.assertEqual(native['execution']['versions'], self.plan['runtime_versions']['npu'])
        self.assertEqual(bundle['prediction']['token_id'],2)
        self.assertEqual(bundle['capture_seconds'], 0.01)
        self.assertEqual(bundle['prediction_bytes'], 24)
        self.assertEqual(bundle['tensor_bytes'], (folder / 'target-0.pt').stat().st_size)

    def test_missing_slot_is_incomplete(self):
        _, seal = self.run_fixture(False)
        report = self.call(seal, 1)
        self.assertEqual(report['status'],'incomplete')
        bundle = json.loads(Path(report['tensor_bundle']['path']).read_bytes())
        self.assertEqual(bundle['missing'],[{'call':self.call_name,'slot':'out'}])

    def test_unplanned_native_tensor_fails(self):
        _, seal = self.run_fixture()
        (self.runtime/'dump/native/step3/rank0/leaked.pt').write_bytes(b'x')
        report = self.call(seal, 1)
        self.assertNotIn('tensor_bundle', report)

    def test_nested_unplanned_payload_fails(self):
        folder, seal = self.run_fixture()
        nested = folder / 'unexpected'
        nested.mkdir()
        (nested / 'extra.npy').write_bytes(b'leak')
        report = self.call(seal, 1)
        self.assertNotIn('tensor_bundle', report)

    def test_reject_unknown_profile_or_unbound_plan(self):
        self.call({**self.arm,'profile':'unknown'},2)
        self.call({**self.arm,'sample_key':'3'},1)
        self.call({**self.arm,'tensor_plan':{**self.plan_ref,'sha256':'0'*64}},1)
        self.assertFalse((self.runtime/'active.json').exists())
