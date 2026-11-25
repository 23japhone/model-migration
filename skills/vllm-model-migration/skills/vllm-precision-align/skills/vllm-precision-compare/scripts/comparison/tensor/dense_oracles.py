"""单请求完整稠密 Q/K/V 的 FP64 attention math 前向与常规归一化。"""
from decimal import Decimal
import math


SDPA_ATTRIBUTES = {'layout', 'kv_source', 'dropout_p', 'scale', 'mask_kind', 'causal_alignment'}


def finite(value, name):
    if type(value) not in (int, float, Decimal) or not math.isfinite(value):
        raise ValueError(name + ' 须为实际有限数值')
    return float(value)


def layer_norm(args, attrs, torch):
    shape = attrs['normalized_shape']
    if not isinstance(shape, list) or not shape or any(type(n) is not int or n <= 0 for n in shape):
        raise ValueError('LayerNorm 需要实际非空的 normalized_shape')
    eps = finite(attrs['eps'], 'LayerNorm eps')
    if eps < 0 or list(args['x'].shape[-len(shape):]) != shape:
        raise ValueError('LayerNorm 的 eps 或归一化维度无效')
    return torch.nn.functional.layer_norm(args['x'], shape, args.get('weight'), args.get('bias'), eps)


def gelu(args, attrs, torch):
    mode = attrs['approximate']
    if mode not in ('none', 'tanh'):
        raise ValueError('GELU approximate 需要与实际 none 或 tanh 一致')
    return torch.nn.functional.gelu(args['x'], approximate=mode)


def dense_heads(q, k, v):
    if any(t.ndim != 4 or not t.is_floating_point() or not t.numel() for t in (q, k, v)):
        raise ValueError('需要非空、四维浮点 Q/K/V')
    if q.shape[:2] != k.shape[:2] or k.shape[:3] != v.shape[:3] or q.shape[-1] != k.shape[-1]:
        raise ValueError('Q/K/V head 或通道未对应，需提供已核验的稠密同 head 输入')


def full_inputs(args, attrs, plan):
    if set(attrs) != SDPA_ATTRIBUTES or attrs['layout'] != 'BHSD' or attrs['kv_source'] != 'full_prefix':
        raise ValueError('SDPA 需要明确的 BHSD 稠密布局、全部有效历史 K/V 及实际计算参数')
    if finite(attrs['dropout_p'], 'dropout_p') != 0:
        raise ValueError('随机 dropout 的 SDPA 未复算')
    q, k, v = (args[name] for name in ('q', 'k', 'v'))
    dense_heads(q, k, v)
    length, history = q.shape[2], k.shape[2]
    if q.shape[0] != 1 or history != len(plan['prefix_token_ids']) or length > history:
        raise ValueError('K/V 必须覆盖目标请求的全部有效前文，且只包含本请求')
    if plan['position'] > 1 and length != 1:
        raise ValueError('单卡同步 decode 需要一个有效 query 行')
    return q, k, v


def effective_mask(mask, attrs, shape, torch):
    kind, origin = attrs['mask_kind'], attrs['causal_alignment']
    if origin not in ('none', 'upper_left', 'lower_right'):
        raise ValueError('必须明确因果掩码的坐标原点')
    if mask is None:
        if kind != 'none':
            raise ValueError('SDPA 缺少实际掩码张量')
        return None
    if origin != 'none':
        raise ValueError('显式掩码应包含实际因果限制')
    if kind == 'bool_keep' and mask.dtype == torch.bool:
        return torch.broadcast_to(mask, shape)
    if kind == 'additive' and mask.is_floating_point():
        return torch.broadcast_to(mask, shape)
    raise ValueError('SDPA 实际 mask 的类型与保留位或加性语义不符')


def query_mask(mask, origin, start, stop, shape, torch):
    if mask is not None:
        return mask[:, :, start:stop, :]
    if origin == 'none':
        return None
    q_length, k_length = shape[-2:]
    shift = k_length - q_length if origin == 'lower_right' else 0
    query = torch.arange(start, stop, device='cpu').unsqueeze(-1)
    return torch.arange(k_length, device='cpu') <= query + shift


def attention(args, attrs, torch, plan):
    from torch.nn.attention import sdpa_kernel, SDPBackend

    q, k, v = full_inputs(args, attrs, plan)
    shape = (*q.shape[:3], k.shape[2])
    mask = effective_mask(args.get('mask'), attrs, shape, torch)
    scale = None if attrs['scale'] is None else finite(attrs['scale'], 'scale')
    # 仅拆分独立 query 行，每一块都保留同一完整 K/V 及对应的全局掩码坐标。
    rows = max(1, 131072 // (shape[1] * shape[-1]))
    result = torch.empty((*q.shape[:3], v.shape[-1]), dtype=torch.float64, device='cpu')
    with sdpa_kernel(SDPBackend.MATH):
        for start in range(0, shape[2], rows):
            stop = min(shape[2], start + rows)
            active = query_mask(mask, attrs['causal_alignment'], start, stop, shape, torch)
            result[:, :, start:stop] = torch.nn.functional.scaled_dot_product_attention(
                q[:, :, start:stop], k, v, attn_mask=active, dropout_p=0., is_causal=False, scale=scale)
    return result
