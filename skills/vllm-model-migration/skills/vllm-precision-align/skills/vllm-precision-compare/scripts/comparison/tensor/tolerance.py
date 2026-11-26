"""按原件 dtype 选择容差，使用采集包的乘数还原 FP8 数值。"""
from dataclasses import dataclass
from decimal import Decimal
import math
from sys import float_info


DEFAULTS = {
    'float64': (1e-7, 1e-7), 'float32': (1e-5, 1.3e-6),
    'float16': (1e-5, 1e-3), 'bfloat16': (1e-5, 1.6e-2),
    'float8_e4m3fn': (2 ** -9, .125), 'float8_e5m2': (2 ** -16, .25),
}


def multiplier(value):
    if type(value) not in (int, float, Decimal):
        raise ValueError('fp8_scale 乘数须为有限正数')
    try:
        scale = float(value)
    except OverflowError as error:
        raise ValueError('fp8_scale 超出 FP64 范围') from error
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('fp8_scale 乘数须为可表示的有限正数')
    return scale


def scale_grid(spec, shape, torch):
    if not isinstance(spec, dict):
        return multiplier(spec)
    block = spec.get('block_shape')
    if not isinstance(block, list) or len(block) != len(shape) or any(type(n) is not int or n <= 0 for n in block):
        raise ValueError('fp8_scale.block_shape 须逐轴声明正整数块长')
    grid = [(n + width - 1) // width for n, width in zip(shape, block)]
    values = spec.get('values')
    if not isinstance(values, list) or len(values) != math.prod(grid):
        raise ValueError('fp8_scale.values 缺少与所采范围匹配的逐块乘数')
    result = torch.tensor([multiplier(s) for s in values], dtype=torch.float64).reshape(grid)
    for axis, (size, width) in enumerate(zip(shape, block)):
        # 只索引真实位置，末块的宽度可能大于剩余元素数。
        result = result.index_select(axis, torch.arange(size) // min(width, max(size, 1)))
    return result


def slot_scale(row, slot, side, steps, torch, transform):
    if not row['dtype'].startswith('float8'):
        return 1.
    if 'fp8_scale' not in row:
        required = slot.get('fp8_scaled', False)
        if isinstance(required, dict):
            required = required.get(side, False)
        if row.get('fp8_scaled') or required or side in slot.get('fp8_scale_source', {}):
            detail = row.get('fp8_scale_issue', '采集记录未提供比例')
            raise ValueError(f'{side} 的 FP8 张量缺少反量化乘数 fp8_scale：{detail}')
        return 1.
    scale = scale_grid(row['fp8_scale'], row['shape'], torch)
    if torch.is_tensor(scale):
        for step in steps:
            scale = transform(scale, step)
    return scale


@dataclass
class Captured:
    tensor: object
    scale: object
    scale_issue: str = ''

    def needs_wide(self):
        if not str(self.tensor.dtype).startswith('torch.float8') or self.scale_issue:
            return False
        lo, hi = (self.scale, self.scale) if isinstance(self.scale, float) else (self.scale.min().item(), self.scale.max().item())
        # 覆盖两类 FP8 的最小量子 2^-16、最大编码值和双端相减的余量。
        return lo < float_info.min * 65536 or hi > float_info.max / 131072

    def decoded(self):
        if self.scale_issue:
            raise ValueError(self.scale_issue)
        if self.needs_wide():
            raise ValueError('FP8 实际值超出 CPU FP64 参考可分辨范围')
        if str(self.tensor.dtype).startswith('torch.float8'):
            return self.tensor.double() * self.scale
        return self.tensor

    def tolerances(self, overrides):
        if not self.tensor.is_floating_point():
            return {'atol': 0, 'rtol': 0}
        dtype = str(self.tensor.dtype).removeprefix('torch.')
        if dtype not in DEFAULTS and overrides.keys() != {'atol','rtol'}:
            raise ValueError('未定义浮点类型的缺省容差：' + dtype)
        atol, rtol = DEFAULTS.get(dtype, (0, 0))
        return {'atol': overrides['atol'] if 'atol' in overrides else atol * self.scale,
                'rtol': overrides.get('rtol', rtol)}


def describe(limits, torch):
    atol = limits['atol']
    if torch.is_tensor(atol):
        atol = {'min': atol.min().item(), 'max': atol.max().item(), 'basis': 'per_block_scale'}
    return {'atol': atol, 'rtol': limits['rtol']}
