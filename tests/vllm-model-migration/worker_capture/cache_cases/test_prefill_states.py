"""以真实 CPU 张量复核跨页写入、无块表读、分块初态与递归写回。"""
from contextlib import ExitStack
from importlib.util import find_spec
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.test_runtime_closure import VERIFY
sys.path.insert(0, str(VERIFY.parent))

from verification.runtime.capture.adapters.decode_cache import plan
from verification.runtime.capture.adapters.decode_cache.prefill_attention import PrefillAttention
from verification.runtime.capture.adapters.decode_cache.prefill_gdn import PrefillGDN, bind_prefill_gdn, PREFILL_OPS
from verification.runtime.capture.adapters.decode_cache.snapshots import Snapshots, observer_only
from verification.runtime.capture import layer_evidence


def chunk_gated_delta_rule(**args):
    initial = args['initial_state']
    return None, initial + 4


class Chunk:
    def _forward_core(self):
        row = self.row
        torch.ops._C_ascend.npu_causal_conv1d_custom(None, conv_state=self.kv_cache[0], run_mode=0,
            query_start_loc_opt=(0, 2), cache_indices_opt=(row,), initial_state_mode_opt=(int(self.initial),))
        physical = self.kv_cache[1][row]
        initial = physical.T.clone().unsqueeze(0)
        if not self.initial:
            initial.zero_()
        _, final = chunk_gated_delta_rule(initial_state=initial,cu_seqlens=self.offsets,output_final_state=True)
        physical.copy_(final[0].T)


@unittest.skipUnless(find_spec('torch'), '需要 CPU PyTorch')
class PrefillStates(unittest.TestCase):
    def setUp(self):
        import torch as cpu
        from worker_capture.cache_cases import runner
        global torch
        torch = runner.torch
        self.cpu = cpu
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = Snapshots(self.folder, 65536, runner.Watch)
        self.enterContext(patch.dict(sys.modules, {'msprobe.core.dump.hook_manager': NS(
            BaseHookManager=NS(inner_switch=runner.SWITCH))}))
        self.group=dict(blocks=[2,0],page_size=4,group_id=0,layer='L')
        self.schedule=dict(position=2,count=3,groups=[dict(self.group,layers=['L'])])

    def attention(self, start, slots, cached):
        t = self.cpu
        schedule=dict(self.schedule,position=start)
        obj=PrefillAttention(self.store,self.group,schedule,2,'ChunkedPrefill' if cached else 'PrefillNoCache')
        kv=t.zeros(2,8,2,1,2)
        key=t.arange(6,dtype=t.float32).reshape(3,1,2)
        args=dict(key=key,value=-key,key_cache=kv[0],value_cache=kv[1],slot_mapping=t.tensor(slots))
        with observer_only():
            obj.before_write(args)
        for i, slot in enumerate(slots):
            for name in ('key','value'):
                args[name+'_cache'][slot//2,slot%2].copy_(args[name][i])
        with observer_only():
            obj.after_write()
            expected=plan.attention_plan(self.group['blocks'],4,2,start+2)
            table=t.tensor([expected['expected_read_blocks']]) if cached else None
            obj.read(dict(key=kv[0].view(8,2,-1) if cached else key,
                          value=kv[1].view(8,2,-1) if cached else args['value'],
                          block_size=2,block_table=table,sparse_mode=3,input_layout='TND',
                          actual_seq_lengths=[3],actual_seq_lengths_kv=[start+3]))
        return obj.event

    def test_cross_page_and_noncontiguous_physical_blocks(self):
        event=self.attention(2,[10,11,0],True)
        layer_evidence.verify_prefill(event,self.group,self.schedule)
        self.assertEqual(event['read_slot_ranges'],[[8,10],[10,12],[0,1]])
        self.assertEqual(event['value']['matches_by_position'],[True]*3)
        self.assertEqual(self.store.files['key.after']['shape'],[3,1,2])

    def test_direct_inputs_and_wrong_slot_are_distinguishable(self):
        event=self.attention(0,[8,9,13],False)
        self.assertEqual(event['read_source'],'input_kv')
        self.assertFalse(event['write_plan_matches'])
        self.assertEqual(event['expected_slots'],[8,9,10])
        self.assertTrue(event['key']['matches_input'])

    def test_missing_snapshots_identify_partial_state(self):
        state = {'kind':'gdn','module':'m.gdn','snapshots':{'ssm.before': {}},
                 'event':{'observation':'prefill_gdn'}}
        call = {'occurrence':0,'states':[state],'boundary':{'input':{}}}
        layer_evidence.describe_missing(call)
        self.assertEqual(call['missing_boundary'], ['output'])
        self.assertEqual(state['missing_snapshots'], ['conv.after','conv.before','ssm.after','ssm.initial'])

    def test_request_budget_spans_forwards_and_resets_between_requests(self):
        from verification.runtime.capture.adapters.decode_cache import runtime, layer_capture
        from worker_capture.session_fixture import write
        from worker_capture.cache_cases import runner
        rec=NS(layer_modules={}, architecture={'layers':[]},cache_watch=runner.Watch,cache_budget=40)
        for i in range(3):
            folder=self.folder/('one' if i<2 else 'two');folder.mkdir(exist_ok=True)
            write(folder/'intent.json',{'cache_capture':{'mode':'full_request','max_request_snapshot_bytes':20}})
            rec.frame={'forward_id':str(i)};rec.folder=folder
            runtime.selected(rec,NS())
            record={'errors':[],'layers':[]}
            def allocate(directory,budget,watch):
                store=Snapshots(directory,budget,watch)
                for j in range(3):
                    try: store.save(str(j),self.cpu.ones(1))
                    except ValueError: break
                return store
            with ExitStack() as stack, patch.object(layer_capture,'Snapshots',side_effect=allocate):
                layer_capture.install_layers(stack,rec,record,{'phase':'prefill','groups':[]},{})
            self.assertEqual(record['snapshot_bytes'],12 if i!=1 else 8)
            self.assertEqual(rec.request_remaining,8 if i!=1 else 0)

    def test_chunked_gdn_clears_initial_state_then_continues(self):
        t=self.cpu
        layer=Chunk()
        layer.kv_cache=[t.ones(4,2),t.ones(4,2,3)]
        layer.row=2
        layer.offsets=t.tensor([0,2])
        previous=None
        for start in (0,2):
            store=self.store.scope(str(start))
            layer.initial=bool(start)
            meta=NS(non_spec_state_indices_tensor=t.tensor([2]),has_initial_state=t.tensor([layer.initial]))
            capture=PrefillGDN(store,self.group,dict(position=start,count=2),'none',meta,layer.kv_cache)
            seen,errors=[],[]
            with ExitStack() as stack:
                bind_prefill_gdn(stack,layer,capture,seen,errors)
                layer._forward_core()
            self.assertFalse(errors,errors)
            self.assertEqual(seen,PREFILL_OPS)
            self.assertEqual(store.files['ssm.initial']['preview'],[4.]*6 if start else [0.]*6)
            if previous:
                self.assertEqual(store.files['ssm.before']['sha256'],previous)
            previous=store.files['ssm.after']['sha256']
        self.assertNotIn('_forward_core',vars(layer))
