"""用于公开入口验证的 CPU 模型。替代 Ascend 两类算子，存储及原位更新使用 PyTorch。"""
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

import torch
from torch import nn

from . import runner as ops
from verification.runtime.capture.adapters import architecture
from verification.runtime.capture.adapters.decode_cache import plan, runtime


class Attention(nn.Module):
    def __init__(self, path):
        super().__init__()
        self.layer_name = path
        self.impl = ops.Full()
        self.key, self.value = torch.zeros((2, 40, 4, 1, 2)).unbind()

    def forward(self, x):
        self.impl.reshape_and_cache(slot_mapping=torch.tensor([52]), key=x.reshape(1, 1, 2),
                                    value=-x.reshape(1, 1, 2), key_cache=self.key, value_cache=self.value)
        self.impl.forward_fused_infer_attention(query=x, key=self.key.view(40, 4, -1),
            value=self.value.view(40, 4, -1), block_size=4, block_table=torch.tensor([[32, 33, 34, 35, 12, 13]]),
            actual_seq_lengths=[1], actual_seq_lengths_kv=[21], sparse_mode=3, input_layout='TND')
        return x + 1


class GDN(nn.Module, ops.Linear):
    def __init__(self, path):
        super().__init__()
        self.prefix = path
        self.kv_cache = [torch.ones(12, 3), torch.ones(12, 2, 2)]

    def forward(self, x):
        self._forward_core(7)
        return x + 1


class Block(nn.Module):
    def __init__(self, i, stack, gdn):
        super().__init__()
        path = f'{stack}.{i}.state'
        self.state = GDN(path) if gdn else Attention(path)

    def forward(self, x):
        return self.state(x)


class Decoder(nn.Module):
    def __init__(self, count, hybrid=True, stack='layers'):
        super().__init__()
        self.model = nn.Module()
        blocks = nn.ModuleList(Block(i, 'model.' + stack, hybrid and i % 4 != 3) for i in range(count))
        setattr(self.model, stack, blocks)
        self.stack = stack
        self.config = NS(num_hidden_layers=count)

    def forward(self, x):
        for block in getattr(self.model, self.stack):
            x = block(x)
        return x


def inspect(model):
    from verification.runtime.capture.adapters.decode_cache.backends import BY_KIND
    replacements = {architecture.class_name(ops.Full()): BY_KIND['attention'], GDN.__module__ + '.GDN': BY_KIND['gdn']}
    with patch.dict(architecture.BY_CLASS, replacements):
        return architecture.discover(model, model.config)


def execute(model, graph, folder, frame, budget=1048576):
    states = [s for row in graph['layers'] for s in row['states']]
    def names(kind):
        return [s['address'] for s in states if s['kind'] == kind]
    groups = [NS(layer_names=names(kind), kv_cache_spec=NS(block_size=16)) for kind in ('attention', 'gdn')]
    metas = {name: NS(attn_state=NS(name='DecodeOnly'), num_actual_tokens=1) for name in names('attention')}
    metas.update({name: NS(num_decodes=1, num_prefills=0, num_spec_decodes=0,
                           non_spec_state_indices_tensor=torch.tensor([7])) for name in names('gdn')})
    runner = NS(kv_cache_config=NS(kv_cache_groups=groups), cache_config=NS(mamba_cache_mode='none'),
                input_batch=NS(block_table=[NS(block_size=4), NS(block_size=16)]))
    rec = NS(runner=runner, folder=folder, frame=frame, architecture=graph, layer_modules=dict(model.named_modules()),
             backend_errors={}, cache_budget=budget, cache_watch=ops.Watch)
    scheduler = NS(kv_cache_manager=NS(get_block_ids=lambda _: ([8, 3], [7, 8])), kv_cache_config=runner.kv_cache_config)
    req = frame['internal_id']
    output = NS(scheduled_cached_reqs=NS(req_ids=[req], num_output_tokens=[1], num_computed_tokens=[20]),
                num_scheduled_tokens={req: 1})
    schedule = plan.snapshot(scheduler, output, req, 1)
    runtime.state.write(folder / ('schedule-' + schedule['schedule_id'] + '.json'), schedule)
    output._verify_cache_schedule_id = schedule['schedule_id']
    runtime.selected(rec, output)
    modules = {'msprobe.core.dump.hook_manager': NS(BaseHookManager=NS(inner_switch=ops.SWITCH)),
               'vllm.forward_context': NS(get_forward_context=lambda: NS(attn_metadata=metas))}
    with patch.dict(sys.modules, modules), runtime.observe_decode(rec):
        return model(torch.tensor([[1., 2.]]))
