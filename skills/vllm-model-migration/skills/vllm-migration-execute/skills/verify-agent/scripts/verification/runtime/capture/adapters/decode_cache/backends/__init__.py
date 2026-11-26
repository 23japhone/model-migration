"""按 worker 实际加载的实现匹配缓存语义。"""
from dataclasses import dataclass
from typing import Callable

from . import bindings
from .mla_evidence import operations as mla_operations
from ..prefill_gdn import PREFILL_OPS


@dataclass(frozen=True)
class Backend:
    kind: str
    implementation: str
    methods: tuple
    files: frozenset
    bind: Callable
    operations: Callable
    dependencies: tuple = ()
    direct_address: str | None = None


def attention_operations(event):
    return ['reshape_and_cache', 'fia']


def gdn_operations(event):
    if event.get('observation') == 'prefill_gdn':
        return PREFILL_OPS
    return ['npu_causal_conv1d_custom', 'npu_recurrent_gated_delta_rule']


DEVICE = 'vllm_ascend.device.device_op'
ATTENTION = Backend('attention', 'vllm_ascend.attention.attention_v1.AscendAttentionBackendImpl',
                    ('reshape_and_cache', 'forward_fused_infer_attention'),
                    frozenset({'key.input', 'key.after', 'value.input', 'value.after'}), bindings.attention, attention_operations, (DEVICE,))
GDN = Backend('gdn', 'vllm_ascend.ops.gdn.AscendGatedDeltaNetAttention', ('_forward_core',),
              frozenset({'conv.before', 'conv.after', 'ssm.before', 'ssm.after'}), bindings.gdn, gdn_operations, direct_address='prefix')
MLA = Backend('mla', 'vllm_ascend.attention.mla_v1.AscendMLAImpl',
              ('exec_kv_prefill', 'exec_kv_decode', '_forward_prefill', '_forward_decode', '_compute_prefill_context'),
              frozenset({'mla.projection.input', 'mla.latent.after', 'mla.rope.after'}), bindings.mla, mla_operations,
              (DEVICE, 'vllm_ascend.ops.mla'))
BY_CLASS = {item.implementation: item for item in (ATTENTION, GDN, MLA)}
BY_KIND = {item.kind: item for item in BY_CLASS.values()}
MLA_OUTPUTS = {'mla.latent.output', 'mla.rope.output'}

STATE_SNAPSHOTS = frozenset().union(*(b.files for b in BY_KIND.values()), MLA_OUTPUTS, {'ssm.initial', 'key.prefix', 'value.prefix', 'mla.latent.prefix', 'mla.rope.prefix'})
