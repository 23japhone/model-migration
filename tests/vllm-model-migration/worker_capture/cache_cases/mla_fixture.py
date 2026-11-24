"""用小张量和不连续页验证采集链，算子替身仅用于离线契约测试。"""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import sys
import torch

from .runner import SWITCH, Watch
from verification.runtime.capture.adapters.decode_cache.backends.mla import bind_mla
from verification.runtime.capture.adapters.decode_cache.snapshots import Snapshots


def fused(x, weight, cos, sin, slots, rope, latent, **kwargs):
    keys = x[..., :4].reshape(-1, 1, 4) + 7
    rotations = x[..., 4:].reshape(-1, 1, 2) - 2
    for c, v in ((latent, keys), (rope, rotations)):
        c.flatten(0, 1)[slots] = v
    return rope, latent, rotations, keys


torch_npu = NS(npu_kv_rmsnorm_rope_cache=fused, npu_fused_infer_attention_score=lambda *a, **k: None,
               npu_fused_infer_attention_score_v2=lambda *a, **k: None)
DeviceOperator = NS(kv_cache_load=lambda *a, **k: None)


class Impl:
    kv_lora_rank = 4
    qk_rope_head_dim = 2

    def exec_kv_prefill(self, x, slots, a, b):
        return torch_npu.npu_kv_rmsnorm_rope_cache(x, None, None, None, slots, b, a, cache_mode='PA', is_output_kv=True)

    def exec_kv_decode(self, x, slots, a, b):
        return torch_npu.npu_kv_rmsnorm_rope_cache(x, None, None, None, slots, b, a, cache_mode='PA')

    def _forward_prefill(self, n):
        torch_npu.npu_fused_infer_attention_score(None, None, None, actual_seq_lengths=[n], actual_seq_lengths_kv=[n],
            block_table=None, atten_mask=True, sparse_mode=3, input_layout='TND')

    def _compute_prefill_context(self, a, b, table, n, history):
        DeviceOperator.kv_cache_load(a, b, table, [history], [0], key=a[0, :history], value=b[0, :history])
        torch_npu.npu_fused_infer_attention_score(None, None, None, actual_seq_lengths=[n],
            actual_seq_lengths_kv=[history], atten_mask=None, sparse_mode=0, input_layout='TND')

    def _forward_decode(self, a, b, table, end):
        k = a.view(-1, 1, 128, 4)
        torch_npu.npu_fused_infer_attention_score_v2(None, k, k, key_rope=b.view(-1, 1, 128, 2), block_table=table,
            actual_seq_kvlen=[end], input_layout='BNSD_NBSD', sparse_mode=0, atten_mask=None, block_size=128)


def run(folder, start, count, prefill, *, wrong_slot=False, history=True, budget=10000):
    group = {'group_id': 0, 'layer': 'model.layers.0.attn', 'blocks': [3, 1], 'page_size': 128}
    plan = {'position': start, 'count': count}
    result = {'ops': [], 'errors': []}
    module = NS(kv_cache=[torch.zeros((4,128,1,w), dtype=torch.bfloat16) for w in (4,2)], impl=Impl())
    store = Snapshots(Path(folder), budget, Watch)
    ids = torch.tensor([group['blocks'][p // 128] * 128 + p % 128 for p in range(start,start+count)])
    meta = NS(num_actual_tokens=count, num_decodes=int(not prefill), num_prefills=int(prefill), slot_mapping=ids)
    switch = NS(BaseHookManager=NS(inner_switch=SWITCH))
    impl = module.impl
    with patch.dict(sys.modules, {'msprobe.core.dump.hook_manager': switch}), ExitStack() as ctx:
        capture = bind_mla(ctx, module, store, group, plan, meta, result)
        x = torch.arange(count*6, dtype=torch.bfloat16).reshape(count,1,1,6)
        write = impl.exec_kv_prefill if prefill else impl.exec_kv_decode
        write(x, ids + int(wrong_slot), *module.kv_cache)
        table = torch.tensor([group['blocks']])
        if prefill:
            impl._forward_prefill(count)
            if start and history:
                impl._compute_prefill_context(*module.kv_cache, table, count, start)
        else:
            impl._forward_decode(*module.kv_cache, table, start+count)
    assert 'exec_kv_prefill' not in vars(impl) and '_compute_prefill_context' not in vars(impl)
    return capture.event, store.files, result
