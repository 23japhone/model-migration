"""用真实 CPU 张量模拟两个 Ascend 更新边界，运行一次原位写操作。"""
from collections import defaultdict
from contextlib import contextmanager
import hashlib
from threading import get_ident
from types import SimpleNamespace as NS
from unittest.mock import patch
import sys

import torch as cpu

from verification.runtime.capture.adapters.decode_cache import plan, runtime

FULL = 'model.layers.3.self_attn.attn'
LINEAR = 'model.layers.0.linear_attn'
SWITCH = defaultdict(bool)


class Watch:
    """对照上游接口的离线替身，独立读取同一视图，不模拟设备。"""
    def watch(self, name, view):
        assert SWITCH[get_ident()]
        self.view = view
        self.digest = self.hash()
        return NS(digest=self.digest)

    def hash(self):
        return hashlib.sha256(self.view.view(cpu.uint8).numpy().tobytes()).hexdigest()

    def check(self, **kwargs):
        current = self.hash()
        return {'rebound': False, 'changed': current != self.digest,
                'changes': [{'digest_after': current}] if current != self.digest else []}


def write_cache(**args):
    assert not SWITCH[get_ident()]
    block, offset = divmod(args['slot_mapping'].item(), 4)
    for name in ('key', 'value'):
        args[name + '_cache'][block, offset].copy_(args[name][0])


DeviceOperator = NS(reshape_and_cache=write_cache)
torch_npu = NS(npu_fused_infer_attention_score=lambda **kwargs: kwargs['query'])


def conv(*args, **kwargs):
    assert not SWITCH[get_ident()]
    kwargs['conv_state'][kwargs['cache_indices_opt'][0]].add_(2)


def recurrent(**kwargs):
    assert not SWITCH[get_ident()]
    kwargs['state'][kwargs['ssm_state_indices'].item()].mul_(3)


torch = NS(ops=NS(_C_ascend=NS(npu_causal_conv1d_custom=conv, npu_recurrent_gated_delta_rule=recurrent)))


class Full:
    def reshape_and_cache(self, **args):
        DeviceOperator.reshape_and_cache(**args)

    def forward_fused_infer_attention(self, **args):
        return torch_npu.npu_fused_infer_attention_score(**args)


class Linear:
    prefix = LINEAR

    def _forward_core(self, row):
        torch.ops._C_ascend.npu_causal_conv1d_custom(None, None, None, conv_state=self.kv_cache[0],
            cache_indices_opt=(row,), query_start_loc_opt=(0, 1), run_mode=1)
        torch.ops._C_ascend.npu_recurrent_gated_delta_rule(state=self.kv_cache[1], ssm_state_indices=cpu.tensor([row]),
                                                           actual_seq_lengths=cpu.tensor([0, 1]))


class Decode:
    def __init__(self, folder, frame, budget=65536, watchpoint=Watch):
        self.folder, self.frame = folder, frame
        self.impl, self.linear = Full(), Linear()
        self.linear.kv_cache = [cpu.ones(12, 3, dtype=cpu.bfloat16), cpu.full((12, 2, 2), 1.5)]
        self.key, self.value = cpu.zeros((2, 40, 4, 1, 2), dtype=cpu.bfloat16).unbind()
        groups = [NS(layer_names=[n], kv_cache_spec=NS(block_size=16)) for n in (FULL, LINEAR)]
        buffers = NS(offset=0, src_ptrs=NS(np=cpu.zeros(0)), dst_ptrs=NS(np=cpu.zeros(0)), sizes=NS(np=cpu.zeros(0)))
        runner = NS(kv_cache_config=NS(kv_cache_groups=groups), cache_config=NS(mamba_cache_mode='align'),
                    _mamba_copy_bufs=buffers, input_batch=NS(block_table=[NS(block_size=4), NS(block_size=16)]))
        layers = ((FULL, NS(layer_name=FULL, impl=self.impl)), (LINEAR, self.linear))
        self.rec = NS(frame=frame, folder=folder, runner=runner, cache_targets=layers,
                      cache_budget=budget, cache_watch=watchpoint)
        self.meta = {FULL: NS(attn_state=NS(name='DecodeOnly'), num_actual_tokens=1),
                     LINEAR: NS(num_decodes=1, num_prefills=0, num_spec_decodes=0, non_spec_state_indices_tensor=cpu.tensor([7]))}

    def schedule(self):
        req = self.frame['internal_id']
        data = NS(req_ids=[req], num_output_tokens=[1], num_computed_tokens=[20])
        output = NS(num_scheduled_tokens={req: 1}, scheduled_cached_reqs=data)
        class Scheduler:
            def schedule(self):
                return output
        manager = Scheduler()
        manager.kv_cache_manager = NS(get_block_ids=lambda req_id: ([8, 3], [4, 7]))
        manager.kv_cache_config = self.rec.runner.kv_cache_config
        with patch.object(plan.state, 'mapped', return_value=(self.folder, req)):
            plan.install_schedule(Scheduler)
            runtime.selected(self.rec, manager.schedule())

    @contextmanager
    def observing(self):
        modules = {'msprobe.core.dump.hook_manager': NS(BaseHookManager=NS(inner_switch=SWITCH)),
                   'vllm.forward_context': NS(get_forward_context=lambda: NS(attn_metadata=self.meta))}
        with patch.dict(sys.modules, modules), runtime.observe_decode(self.rec):
            yield

    def run(self, slot=52, row=7, last_read_block=13):
        self.schedule()
        with self.observing():
            self.linear._forward_core(row)
            self.impl.reshape_and_cache(slot_mapping=cpu.tensor([slot]), key=cpu.tensor([[[1, 2]]], dtype=cpu.bfloat16),
                                        value=cpu.tensor([[[3, 4]]], dtype=cpu.bfloat16), key_cache=self.key, value_cache=self.value)
            self.read_cache(last_read_block)

    def read_cache(self, last_read_block):
        self.impl.forward_fused_infer_attention(query=None, key=self.key.view(40, 4, -1), value=self.value.view(40, 4, -1),
            block_size=4, block_table=cpu.tensor([[32, 33, 34, 35, 12, last_read_block, -1]]), actual_seq_lengths=[1],
            actual_seq_lengths_kv=[21], sparse_mode=3, input_layout='TND')
