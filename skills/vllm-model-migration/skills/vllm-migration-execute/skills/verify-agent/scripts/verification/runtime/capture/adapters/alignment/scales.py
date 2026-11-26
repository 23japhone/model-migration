"""在同一次原生钩子中读取 FP8 编码所用的实际反量化乘数。"""


def block_values(scales, widths, info, region, torch):
    original = region.get('original_shape', info['shape'])
    if len(widths) != len(original) or any(type(b) is not int or b <= 0 for b in widths):
        raise ValueError('FP8 block_shape 与张量维数不同或含无效块宽')
    grid = [(n + b - 1) // b for n, b in zip(original, widths)]
    if list(scales.shape) != grid:
        raise ValueError('FP8 实际比例网格的形状与 block_shape 不符')
    widths = list(widths)
    if 'capture_slice' in region:
        clip = region['capture_slice']
        axis = clip['axis']
        indices = torch.arange(clip['start'], clip['stop']) // min(widths[axis], original[axis])
        scales = scales.index_select(axis, indices)
        widths[axis] = 1
    return {'block_shape': widths, 'values': scales.reshape(-1).tolist()}


def capture_scale(slot, hardware, info, region, select):
    binding = slot.get('fp8_scale_source', {}).get(hardware)
    if not info['dtype'].startswith('float8') or binding is None:
        return {}
    import torch
    try:
        path = binding['path']
        if path[0] not in ('input_args','input_kwargs','post_input_args','post_input_kwargs','output','buffers','parameters'):
            raise ValueError('未识别的 FP8 比例实参路径')
        operand = select(path)
        scale = torch.as_tensor(operand, device='cpu', dtype=torch.float64).detach()
        if not torch.all(torch.isfinite(scale) & scale.gt(0)):
            raise ValueError('FP8 比例含无效乘数')
        spec = block_values(scale, binding['block_shape'], info, region, torch) if 'block_shape' in binding else scale.item()
        return {'fp8_scaled': True, 'fp8_scale': spec}
    except (AttributeError, IndexError, KeyError, ValueError, TypeError, RuntimeError, OverflowError) as error:
        return {'fp8_scaled': True, 'fp8_scale_issue': str(error)}
