"""校验单端冻结采集包及原生张量文件。"""
import hashlib
from pathlib import Path

from ..evidence import bound
from .tolerance import Captured, slot_scale
from .contract import capture_region, natural, numbers, phase, text


def package(ref, side, plan):
    bundle = bound(ref)
    data = bundle.value
    if (data.get('format'), data.get('hardware'), data.get('plan_sha256')) != (
            'tensor-bundle-v1', side, plan.ref['sha256']):
        raise ValueError(side + ' 采集包的设备、计划或格式不符')
    for key in ('case', 'position', 'prefix_token_ids', 'model_identity'):
        if data.get(key) != plan.value[key]:
            raise ValueError(side + ' 采集包与计划的 ' + key + ' 不符')
    if data.get('prompt_token_ids', plan.value['prompt_token_ids']) != plan.value['prompt_token_ids']:
        raise ValueError(side + ' 采集包提示不同')
    if data.get('phase') != phase(plan.value) or not natural(data.get('rank')) or data['rank'] != 0:
        raise ValueError(side + ' 采集包的前向阶段或 rank 不符')
    text(data['forward_id'], 'forward_id')
    return bundle


def index(bundle, expected):
    rows = {}
    issues = []
    if 'status' in bundle.value and bundle.value['status'] not in ('complete', 'completed'):
        issues.append('采集端报告本次采集不完整')
    for row in bundle.value['tensors']:
        key = row.get('call'), row.get('slot')
        if key not in expected:
            issues.append('未列入共同范围的张量 ' + str(key))
        elif key in rows:
            rows[key] = None
            issues.append('同一张量被重复登记 ' + str(key))
        else:
            rows[key] = row
    return rows, issues


def tensor_path(bundle, row):
    root = Path(bundle.ref['path']).parent
    rel = row['path']
    if not isinstance(rel, str) or not rel or '\\' in rel:
        raise ValueError('张量路径须为相对文件名')
    path = root / rel
    if Path(rel).is_absolute() or any(x in ('', '.', '..') for x in rel.split('/')) or path.resolve() != path:
        raise ValueError('张量文件路径越界或经重定向')
    return path


def load(stream, suffix, torch):
    if suffix == '.pt':
        return torch.load(stream, map_location='cpu', weights_only=True)
    if suffix == '.npy':
        import numpy as np
        return torch.from_numpy(np.load(stream, allow_pickle=False))
    raise ValueError('仅支持真实 .pt 和 .npy 张量')


def read_tensor(path, checksum, torch):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise ValueError('张量文件摘要不符')
        stream.seek(0)
        return load(stream, path.suffix, torch)


def verify_region(row, planned):
    if row.get('capture_slice') != planned:
        raise ValueError('实际落盘切片与计划不同')
    if planned is None:
        if 'original_shape' in row:
            raise ValueError('未计划切片却声明原始形状')
        return
    shape = row['original_shape']
    axis = planned['axis']
    if not numbers(shape) or axis >= len(shape) or planned['stop'] > shape[axis]:
        raise ValueError('切片的原始形状无效')
    expected = list(shape)
    expected[axis] = planned['stop'] - planned['start']
    if expected != row['shape']:
        raise ValueError('落盘形状不能对应声明的有效区域')


def verified(bundle, row, slot, torch):
    side = bundle.value['hardware']
    verify_region(row, capture_region(slot, side))
    tensor = read_tensor(tensor_path(bundle, row), row['sha256'], torch)
    if type(tensor) not in (torch.Tensor, torch.nn.Parameter) or tensor.device.type != 'cpu':
        raise ValueError('文件须包含一个真实 CPU 张量')
    if row.get('layout') != 'strided' or tensor.layout != torch.strided or tensor.is_quantized or tensor.is_complex():
        raise ValueError('当前比较需要实数或布尔 strided 张量')
    if tensor.storage_offset() or tensor.untyped_storage().nbytes() != tensor.numel() * tensor.element_size():
        raise ValueError('原件携带所选张量之外的底层存储，须在目标前向只保存该逻辑范围')
    if not isinstance(row['shape'], list) or not all(natural(dim) for dim in row['shape']):
        raise ValueError('张量形状无效')
    if row['dtype'] != str(tensor.dtype).removeprefix('torch.') or row['shape'] != list(tensor.shape):
        raise ValueError('张量原件与 shape 或 dtype 声明不符')
    return normalized(tensor.detach(), row, slot, side, torch)


def normalized(result, row, slot, side, torch):
    steps = slot.get('transforms', {}).get(side, [])
    for step in steps:
        result = transform(result, step)
    try:
        scale = slot_scale(row, slot, side, steps, torch, transform)
    except ValueError as error:
        return Captured(result, None, str(error))
    return Captured(result, scale)


def transform(tensor, step):
    if 'permute' in step:
        axes = step['permute']
        if tensor.ndim != len(axes):
            raise ValueError('轴排列与实际张量维数不符')
        return tensor.permute(*axes)
    region = step['slice']
    axis, lo, hi = (region[k] for k in ('axis', 'start', 'stop'))
    if axis >= tensor.ndim or hi > tensor.shape[axis]:
        raise ValueError('有效区域切片越界')
    return tensor.narrow(axis, lo, hi - lo)
