"""目标前向及 msProbe 原生回调的逐槽过滤。"""
from pathlib import Path
import os
import sys
import tempfile
from threading import get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))
from verification.runtime.capture.adapters.alignment.gate import TargetGate
from verification.runtime.capture.adapters.alignment.plan import is_target


class TargetCapture(unittest.TestCase):
    def test_prefill_chunk_and_requested_prefix(self):
        plan = dict(position=1, prompt_token_ids=[4, 5, 6], prefix_token_ids=[4, 5, 6])
        frame = dict(input_ids=[4], positions=[0], output_index=0, logits_indices=[0])
        self.assertFalse(is_target(plan, frame, [4, 5, 6], []))
        frame.update(input_ids=[5, 6], positions=[1, 2], logits_indices=[1])
        self.assertTrue(is_target(plan, frame, [4, 5, 6], []))
        frame.update(input_ids=[7], positions=[3], output_index=1, logits_indices=[0])
        plan.update(position=2, prefix_token_ids=[4, 5, 6, 8])
        with self.assertRaises(ValueError):
            is_target(plan, frame, [4, 5, 6], [7])

    def test_repeated_apis_save_only_second_in_specified_owner(self):
        import torch
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        plan = {'calls': [{'id': 'call', 'kind': 'api', 'npu': {'name': 'Functional.linear', 'owner': 'layers.1'},
                           'occurrence': 1, 'slots': [{'id': 'x', 'role':'input','npu':['input_args',0]},
                                                     {'id':'out','role':'output','npu':['output',0]}]}]}
        processor = SimpleNamespace(api_parent_node={})
        orig_input, orig_output = Mock(), Mock()
        collector = SimpleNamespace(module_processor=processor, forward_input_data_collect=orig_input,
                                    forward_output_data_collect=orig_output, should_collect=Mock())
        gate = TargetGate(plan, folder)
        io = SimpleNamespace(args=(torch.tensor([2.]), object()), kwargs={}, output=torch.tensor([3.]))
        with gate.intercept(collector):
            for i, path in enumerate(('layers.0', 'layers.1', 'layers.1')):
                processor.api_parent_node[get_ident()] = 'Module.' + path + '.Block.forward.0'
                name = 'Functional.linear.' + str(i) + '.forward'
                collector.forward_input_data_collect(name, None, os.getpid(), io)
                collector.forward_output_data_collect(name, None, os.getpid(), io)
                self.assertEqual(len(list(folder.glob('*.pt'))), 2 if i == 2 else 0)
        self.assertEqual([r['slot'] for r in gate.rows], ['x', 'out'])
        self.assertEqual(gate.errors, [])
        orig_input.assert_not_called()
        orig_output.assert_not_called()
        self.assertIs(collector.forward_input_data_collect, orig_input)

    def test_write_after_scalar_and_compact_slice_share_native_call_identity(self):
        import torch
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        call = {'id':'update', 'kind':'api', 'npu':{'name':'Torch.update', 'owner':'m'}, 'occurrence':0,
                'slots':[{'id':'step', 'role':'input', 'npu':['input_kwargs','scale']},
                         {'id':'updated', 'role':'output', 'npu':['post_input_args',0], 'capture_slice':{'axis':0,'start':1,'stop':2}}]}
        gate = TargetGate({'calls':[call]}, folder)
        processor = SimpleNamespace(api_parent_node={get_ident():'Module.m.M.forward.0'})
        tensor = torch.zeros(16, 3)
        io = SimpleNamespace(args=(tensor,), kwargs={'scale':1.25}, output=None)
        gate.observe('input', processor, 'Torch.update.4.forward', None, os.getpid(), io)
        tensor[1] += 3
        gate.observe('output', processor, 'Torch.update.4.forward', None, os.getpid(), io)
        self.assertEqual(gate.errors, [])
        scale, changed = gate.rows
        self.assertEqual((scale['shape'],scale['dtype']), ([], 'float64'))
        self.assertEqual(changed['original_shape'], [16,3])
        self.assertEqual(changed['native_name'], 'Torch.update.4.forward')
        saved = torch.load(folder / changed['path'], weights_only=True)
        self.assertEqual(saved.tolist(), [[3,3,3]])
        self.assertEqual(saved.untyped_storage().nbytes(), 12)

    def test_native_fp8_scale_is_captured_with_its_tensor(self):
        import torch
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        slot = {'id':'x', 'role':'input', 'npu':['input_args',0],
                'fp8_scale_source':{'npu':{'path':['input_kwargs','scales'],'block_shape':[2]}}}
        call = {'id':'op', 'kind':'api', 'npu':{'owner':'m','name':'Torch.quant_op'},
                'occurrence':0,'slots':[slot]}
        gate = TargetGate({'calls':[call]},folder)
        x = torch.zeros(3).to(torch.float8_e4m3fn)
        io = SimpleNamespace(args=(x,),kwargs={'scales':torch.tensor([.5,2.])})
        proc = SimpleNamespace(api_parent_node={get_ident():'Module.m.Test.forward.0'})
        gate.observe('input',proc,'Torch.quant_op.0.forward',None,os.getpid(),io)
        self.assertFalse(gate.errors)
        self.assertEqual(gate.rows[0]['fp8_scale'], {'block_shape':[2], 'values':[.5,2.]})

    def test_recorder_only_calls_probe_and_persists_logits_on_target(self):
        import hashlib
        import json
        import torch
        from unittest.mock import patch
        from verification.runtime.capture.adapters import state, worker
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, VERIFY_EAGER_ROOT=str(root)))
        folder = root/'request'
        folder.mkdir()
        state.write(root/'active.json', {'directory':'request'})
        state.write(folder/'request.json', {'internal_id':'r'})
        plan = dict(format='tensor-plan-v1', model_identity='1'*64, case={'subset':'s','sample_id':1},
                    position=2, prompt_token_ids=[1], prefix_token_ids=[1,0], runtime_versions={'npu':{'torch':'t','vllm':'v','msprobe':'m'}},
                    calls=[dict(id='c', kind='api', occurrence=0, npu={'owner':'m','name':'Functional.relu'},
                                slots=[dict(id='x', role='input', npu=['input_args',0])])])
        raw = json.dumps(plan).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (folder/'tensor-plan.json').write_bytes(raw)
        state.write(folder/'intent.json', {'profile':'tensor_alignment', 'tensor_plan':{'sha256':digest}})
        self.enterContext(patch.object(worker.targeted, 'runtime_versions', return_value=plan['runtime_versions']['npu']))
        self.enterContext(patch.object(worker.targeted, 'check_observer'))
        rec = object.__new__(worker.Recorder)
        data = SimpleNamespace(prompt_token_ids=[1], output_token_ids=[])
        rec.runner = SimpleNamespace(input_batch=SimpleNamespace(req_ids=['r'],sampling_metadata=SimpleNamespace(all_greedy=True)),
                                     requests={'r':data},get_model=Mock(),execute_model_state=SimpleNamespace(logits=torch.tensor([[1.,2.]])))
        rec.size = 2
        rec.input_metadata = lambda ids, pos: rec.frame.update(input_ids=ids,positions=pos,logits_indices=[0],
                                                                output_index=len(data.output_token_ids))
        rec.debugger = Mock()
        rec.debugger.service.current_iter = 0
        rec.debugger.service.config.async_dump = False
        rec.prepare_probe = Mock(return_value=rec.debugger)
        schedule = SimpleNamespace(num_scheduled_tokens={'r':1})
        for idx in range(3):
            rec.select(schedule)
            rec.start([[1, 0, 1][idx]], [idx])
            rec.close()
            rec.logits()
            rec.scores(rec.runner.execute_model_state.logits)
            self.assertEqual(rec.frame.get('targeted',False), idx==1)
            self.assertEqual(set(rec.pending_vectors), {'raw','processed_probs'} if idx==1 else set())
            rec.candidate(torch.tensor(1))
            rec.finish(SimpleNamespace(sampled_token_ids=[[1]],req_id_to_index={'r':0}))
            data.output_token_ids.append(0 if idx==0 else 1)
        data.output_token_ids.clear()
        rec.select(schedule)
        with patch.object(worker.targeted, 'runtime_versions', return_value={'torch':'changed'}):
            rec.target_plan_key = None
            with self.assertRaisesRegex(ValueError,'版本'):
                rec.start([1],[0])
        rec.debugger.start.assert_called_once()
        rec.debugger.stop.assert_called_once()
        self.assertEqual(len(list(folder.glob('*.bin'))),2)

    def test_real_operand_arguments_and_default_match(self):
        import torch
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        call = {'id':'op','kind':'api','npu':{'owner':'layer','name':'Functional.linear',
              'scalar_args':{'bias':None},'arguments_complete':True},'occurrence':0,
              'slots':[{'id':'x','role':'input','npu':['input_args',0]},
                       {'id':'w','role':'weight','npu':['input_args',1]}]}
        gate = TargetGate({'calls':[call]},folder)
        io = SimpleNamespace(args=(torch.ones(3),torch.ones(3),None),kwargs={},output=torch.ones(3))
        proc = SimpleNamespace(api_parent_node={get_ident():'Module.layer.Test.forward.0'})
        gate.observe('input',proc,'Functional.linear.0.forward',None,os.getpid(),io)
        self.assertFalse(gate.errors)
        for row in gate.rows:
            self.assertEqual(row['scalar_args'], {'bias':None})
            self.assertIs(row['arguments_complete'], True)
        self.assertEqual(len(gate.rows), 2)

    def test_runtime_rejects_changed_alpha_or_unplanned_bias_before_writing(self):
        import torch
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, attrs, extra_args, kwargs in [('Torch.add', {}, (), {'alpha':2}),
                                                 ('Functional.linear', {'bias':None}, (torch.ones(3),), {})]:
            call = {'id':'op','kind':'api','npu':{'owner':'layer','name':name,'scalar_args':attrs,
                  'arguments_complete':True},'occurrence':0,'slots':[{'id':'a','role':'input','npu':['input_args',0]},
                  {'id':'b','role':'input','npu':['input_args',1]}, {'id':'out','role':'output','npu':['output',0]}]}
            gate = TargetGate({'calls':[call]},folder)
            io = SimpleNamespace(args=(torch.ones(3),torch.ones(3),*extra_args),kwargs=kwargs,output=torch.ones(3))
            processor = SimpleNamespace(api_parent_node={get_ident():'Module.layer.Test.forward.0'})
            for side in ('input','output'):
                gate.observe(side,processor,name+'.0.forward',None,os.getpid(),io)
            self.assertTrue(gate.errors)
            self.assertFalse(list(folder.glob('*.pt')))
