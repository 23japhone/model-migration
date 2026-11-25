"""按真实层实例安装相应的 Eager 观察点。"""
from ..events import Attention, GDN
from ..hooks import bind_attention, bind_gdn
from ..prefill_attention import PrefillAttention
from ..prefill_gdn import bind_prefill_gdn, PrefillGDN


def attention(stack, rec, module, group, plan, meta, store, result):
    prefill = plan.get('phase') == 'prefill'
    size = rec.runner.input_batch.block_table[group['group_id']].block_size
    backend = meta.attn_state.name
    if meta.num_actual_tokens != plan.get('count', 1):
        raise ValueError('普通注意力有效 Token 数与调度不符')
    if prefill:
        capture = PrefillAttention(store, group, plan, size, backend)
    elif backend == 'DecodeOnly':
        capture = Attention(store, group, plan['position'], size)
    else:
        raise ValueError('普通注意力未进入单 Token decode')
    bind_attention(stack, module.impl, capture, result['ops'], result['errors'])
    return capture


def gdn(stack, rec, module, group, plan, meta, store, result):
    prefill = plan.get('phase') == 'prefill'
    mode = rec.runner.cache_config.mamba_cache_mode
    counts = meta.num_decodes, meta.num_prefills, meta.num_spec_decodes
    if prefill and counts == (0, 1, 0):
        capture = PrefillGDN(store, group, plan, mode, meta, module.kv_cache)
        bind_prefill_gdn(stack, module, capture, result['ops'], result['errors'])
    elif counts == (1, 0, 0) and plan.get('count', 1) == 1:
        capture = GDN(store, group, plan['position'], mode, meta, module.kv_cache)
        bind_gdn(stack, module, capture, result['ops'], result['errors'])
    else:
        raise ValueError('GDN 后端实际阶段与调度区间不符')
    return capture


def mla(stack, rec, module, group, plan, meta, store, result):
    from .mla import bind_mla
    return bind_mla(stack, module, store, group, plan, meta, result)
