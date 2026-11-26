"""逐块计算逐元素误差、异常坐标及有效范围，保留整数精确性。"""
from decimal import Decimal
import math

from .tolerance import describe
from . import wide_fp8


def coordinate(index, shape):
    result = []
    for length in reversed(shape):
        index, value = divmod(index, length)
        result.append(value)
    return list(reversed(result))


def large_difference(actual, ref):
    # 仅在 FP64 相减溢出时计算更宽的十进制诊断值。
    return abs(Decimal.from_float(actual) - Decimal.from_float(ref))


def float_block(left, right, limits, allow_inf, torch):
    a, b = left.double(), right.double()
    finite = torch.isfinite(a) & torch.isfinite(b)
    magnitude = b.abs()
    d = (a - b).abs()
    finite_diff = torch.where(finite, d, -1.)
    # NaN 始终报告异常。有约定时允许同号且同位置的无穷。
    bad = (~finite) & a.ne(b) if allow_inf else ~finite
    atol, rtol = limits['atol'], limits['rtol']
    bound = atol + rtol * magnitude
    mismatch = d > bound
    overflow = finite & torch.isinf(d) & torch.isinf(bound)
    if overflow.any():
        scale = torch.maximum(a[overflow].abs(), b[overflow].abs())
        normalized = (a[overflow] / scale - b[overflow] / scale).abs()
        local_atol = atol[overflow] if torch.is_tensor(atol) else atol
        mismatch[overflow] = normalized > (local_atol / scale + rtol * (magnitude[overflow] / scale))
    bad |= finite & mismatch
    maximum, offset = torch.max(finite_diff, dim=0)
    err = maximum.item()
    if math.isinf(err):
        err = large_difference(a[offset].item(), b[offset].item())
    return bad, err, int(offset), int((~finite).sum())


def integer_block(left, right, torch):
    bad = left.ne(right)
    if not bad.any():
        return bad, 0, 0, 0
    # Python int 的差值保留 64 位整型极值；仅转换本块不一致的部分。
    indices = bad.nonzero().flatten().tolist()
    a, b = left.tolist(), right.tolist()
    error, pos = max(((abs(int(a[i]) - int(b[i])), i) for i in indices), key=lambda pair: pair[0])
    return bad, error, pos, 0


def incompatible(actual, reference, symmetric):
    if actual.shape != reference.shape:
        return {'result': 'fail', 'criterion': 'structure', 'reason': '规范化后形状不同', 'shapes': [list(actual.shape), list(reference.shape)]}
    if actual.is_floating_point() != reference.is_floating_point():
        return {'result': 'fail', 'criterion': 'structure', 'reason': '浮点与离散数据类型不同',
                'dtypes': [str(actual.dtype), str(reference.dtype)]}
    if symmetric and actual.dtype != reference.dtype:
        return {'result': 'fail', 'criterion': 'structure', 'reason': '双端数据类型不同', 'dtypes': [str(actual.dtype), str(reference.dtype)]}

    if not actual.numel():
        return {'result': 'not_evaluated', 'reason': '有效区域为空', 'elements': 0}


def number(value):
    num = value.item()
    if isinstance(num, float) and not math.isfinite(num):
        return str(num)
    return num


def observed(left, right, index, cross_device):
    if index is None:
        return None
    keys = ('npu', 'gpu') if cross_device else ('device', 'cpu')
    return {keys[0]: number(left[index]), keys[1]: number(right[index])}


def summarize(left, right, limits, torch, allow_inf):
    atol = limits['atol']
    flat_atol = atol.reshape(-1) if torch.is_tensor(atol) else None
    total, special, maximum, max_index, first, first_special = 0, 0, None, None, None, None
    for start in range(0, left.numel(), 65536):
        lpart, rpart = left[start:start+65536], right[start:start+65536]
        if left.is_floating_point():
            block = limits if flat_atol is None else {**limits, 'atol': flat_atol[start:start+65536]}
            bad, error, offset, count = float_block(lpart, rpart, block, allow_inf, torch)
        else:
            bad, error, offset, count = integer_block(lpart, rpart, torch)
        total += int(bad.sum())
        special += count
        if count and first_special is None:
            mask = ~(torch.isfinite(lpart) & torch.isfinite(rpart))
            first_special = start + int(mask.nonzero()[0])
        if count < lpart.numel() and (maximum is None or error > maximum):
            maximum, max_index = error, start + offset
        if first is None and bad.any():
            first = start + int(bad.nonzero()[0])
    return total, special, maximum, max_index, first, first_special


def metrics(actual, reference, limits, torch, *, device_pair=False, allow_inf=False):
    ref_tensor = reference.tensor if device_pair else reference
    invalid = incompatible(actual.tensor, ref_tensor, device_pair)
    if invalid:
        return invalid
    if device_pair and (actual.needs_wide() or reference.needs_wide()):
        if actual.scale_issue or reference.scale_issue:
            raise ValueError(actual.scale_issue or reference.scale_issue)
        stats, values, applied = wide_fp8.compare(actual, reference, limits, torch, allow_inf)
    else:
        ref_values = reference.decoded() if device_pair else reference
        left, right = actual.decoded().reshape(-1), ref_values.reshape(-1)
        effective = (reference if device_pair else actual).tolerances(limits)
        stats = summarize(left, right, effective, torch, allow_inf)
        values = lambda i: observed(left, right, i, device_pair)
        applied = describe(effective, torch)
    return value_report(actual.tensor, stats, values, applied)


def value_report(tensor, stats, values, applied):
    total, special, maximum, max_index, first, first_special = stats
    result = 'fail' if total else 'pass'
    loc = lambda index: coordinate(index, tensor.shape) if index is not None else None
    return {'result': result, 'criterion': 'values', 'tolerances': applied, 'elements': tensor.numel(), 'nonfinite_elements': special,
            'max_abs_error': maximum, 'max_abs_coordinate': loc(max_index),
            'max_abs_values': values(max_index),
            'first_nonfinite': loc(first_special), 'nonfinite_values': values(first_special),
            'exceedances': total,
            'exceedance_fraction': total / tensor.numel(),
            'first_exceedance': loc(first), 'first_values': values(first),
            'reason': None}
