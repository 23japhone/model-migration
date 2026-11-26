"""FP8 缩放可能超出 FP64 运算范围时，用有理数比较原始编码的实际值。"""
from decimal import Decimal
from fractions import Fraction
import math

from .tolerance import DEFAULTS


class ScaledValues:
    def __init__(self, captured, torch):
        self.raw = captured.tensor.reshape(-1)
        self.scales = captured.scale.reshape(-1) if torch.is_tensor(captured.scale) else None
        self.scalar = None if self.scales is not None else Fraction(captured.scale)

    def scale(self, index):
        if self.scales is None:
            return self.scalar
        return Fraction(self.scales[index].item())

    def value(self, index):
        code = self.raw[index].item()
        return Fraction(code) * self.scale(index) if math.isfinite(code) else code

    def scale_range(self):
        if self.scales is None:
            return self.scalar, self.scalar
        return Fraction(self.scales.min().item()), Fraction(self.scales.max().item())


def display(value):
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    if value is None:
        return None
    return str(value)


def assess(gpu, npu, absolute, relative, scaled_atol, allow_inf):
    count = special = 0
    peak = peak_index = first_bad = first_special = None
    for index in range(gpu.raw.numel()):
        actual, ref = npu.value(index), gpu.value(index)
        if isinstance(actual, Fraction) and isinstance(ref, Fraction):
            error = abs(actual - ref)
            if peak is None or error > peak:
                peak, peak_index = error, index
            atol = absolute * gpu.scale(index) if scaled_atol else absolute
            failed = error > atol + relative * abs(ref)
        else:
            special += 1
            if first_special is None:
                first_special = index
            failed = not allow_inf or actual != ref
        if failed:
            count += 1
            if first_bad is None:
                first_bad = index
    return count, special, display(peak), peak_index, first_bad, first_special


def compare(actual, reference, overrides, torch, allow_inf):
    npu, gpu = ScaledValues(actual, torch), ScaledValues(reference, torch)
    dtype = str(reference.tensor.dtype).removeprefix('torch.')
    atol, rtol = DEFAULTS.get(dtype, (0, 0))
    absolute = Fraction(overrides.get('atol', atol))
    relative = Fraction(overrides.get('rtol', rtol))
    scaled_atol = 'atol' not in overrides
    stats = assess(gpu, npu, absolute, relative, scaled_atol, allow_inf)
    limits = {'atol': display(absolute), 'rtol': float(relative)}
    if scaled_atol:
        lo, hi = (display(absolute * s) for s in gpu.scale_range())
        limits['atol'] = lo if gpu.scales is None else {'min': lo, 'max': hi, 'basis': 'per_block_scale'}
    def observed(index):
        return None if index is None else {'gpu': display(gpu.value(index)), 'npu': display(npu.value(index))}
    return stats, observed, limits
