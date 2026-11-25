"""Bind HTTP image order, placeholder positions, encoder runs and fusion witnesses."""
import base64
import hashlib
from pathlib import Path

from ....request import image_id
from ..tensors import file_ref


def fusion_positions(feature, positions, mask):
    start, width = feature['offset'], feature['length']
    bitmap = feature.get('is_embed')
    if bitmap is not None and len(bitmap) != width:
        raise ValueError('图像占位长度与掩码不符')
    expected = [p for p in positions if start <= p < start + width and (bitmap is None or bitmap[p-start])]
    observed = [p for p, active in zip(positions, mask, strict=True) if active]
    if observed != expected:
        raise ValueError('实际融合掩码与图像占位区间不符')
    return expected


def images(folder, payload):
    result = []
    for msg in payload.get('messages', []):
        if not isinstance(msg.get('content'), list):
            continue
        for part in msg['content']:
            if part.get('type') != 'image_url':
                continue
            digest = image_id(part)
            uri = part['image_url']['url']
            mime, encoded = uri[5:].split(';base64,', 1)
            path = folder / f'image-{len(result)}.bin'
            raw = base64.b64decode(encoded, validate=True)
            if not path.exists():
                with path.open('xb') as out:
                    out.write(raw)
            ref = file_ref(path, folder)
            if ref['sha256'] != digest:
                raise ValueError('原始图像摘要不符')
            result.append({'image_index': len(result), 'mime': mime, 'bytes': len(raw), **ref})
    if len(result) > 1:
        raise ValueError('只支持单张图片')
    return result


def check_files(directory, vision):
    for name, item in vision['snapshots'].items():
        if Path(name).name != name or item['file'] != name + '.bin':
            raise ValueError('invalid image tensor filename')
        path = directory / item['file']
        ref = file_ref(path, directory)
        if item.get('path', str(path)) != str(path):
            raise ValueError('视觉快照引用跨 rank 或前向')
        if item['sha256'] != ref['sha256'] or item['bytes'] != path.stat().st_size:
            raise ValueError('视觉快照损坏 ' + str(path))
        item.update(ref)


def witness(directory, frame, worker, enabled, calls, errors):
    info = frame['vision']
    layer_paths = [r['path'] for r in worker['architecture']['other_stacks']['visual.blocks']]
    feature = info['feature']
    points = fusion_positions(feature, frame['positions'], info['mask'])
    if points != info['fusion_positions'] or points and info.get('fusion_equal') is not True:
        raise ValueError('视觉嵌入与融合行不同')
    if bool(info['scheduled']) != info['encoder_executed']:
        raise ValueError('实际执行的视觉编码器与调度不符')
    required = required_snapshots(info, points, calls, layer_paths, errors)
    if enabled and required != set(info['snapshots']):
        errors.append('视觉快照清单差异 ' + str(sorted(required ^ info['snapshots'].keys())))
    base = directory / ('vision-' + frame['forward_id'])
    for name, item in list(info['snapshots'].items()):
        try:
            check_files(base, {'snapshots': {name: item}})
        except (OSError, ValueError, KeyError) as error:
            errors.append(f'视觉快照 {name}: {error}')
            del info['snapshots'][name]
    return info


def bind_images(images, timeline, errors, size):
    for image in images:
        members = [rank_image(image, timeline, rank, errors) for rank in range(size)]
        image['ranks'] = members
        for member in members[1:]:
            if member['feature'] != members[0]['feature']:
                errors.append(f"图像的 rank{member['rank']} 与 rank0 绑定了不同的处理器结果或位置")
    return images


def required_snapshots(info, points, calls, layer_paths, errors):
    required = {'fusion.source', 'fusion.result'} if points else set()
    if info['encoder_executed']:
        validate_grid(info)
        modules = ['visual.patch_embed', *layer_paths, 'visual.merger']
        observed = [call['module'] for call in calls if call['module'] in modules]
        if observed != modules and (len(observed) != len(modules) or set(observed) != set(modules)):
            errors.append('msProbe 视觉层实际执行覆盖不完整 ' + str(sorted(set(modules) - set(observed))))
        required.update(f'{p}.{side}' for p in modules for side in ('input', 'output'))
        required.update(('embedding', 'pixels', 'grid'))
    return required


def rank_image(image, timeline, rank, errors):
    from ....evidence import read_bound
    encoder, fusion, features = [], [], []
    positions = []
    encoded = None
    gathered = hashlib.sha256()
    for entry in timeline:
        ref = entry['ranks'][rank].get('row')
        info = read_bound(ref).get('vision') if ref else None
        if not info:
            errors.append(f"{entry['forward_id']} rank{rank} 缺少图文前向证据")
            continue
        features.append(info['feature'])
        if info['encoder_executed']:
            encoder.append(entry['forward_id'])
            encoded = info['snapshots'].get('embedding')
        if info['fusion_positions']:
            fusion.append(entry['forward_id'])
            positions.extend(info['fusion_positions'])
            part = info['snapshots'].get('fusion.source')
            if part:
                gathered.update(Path(part['path']).read_bytes())
    if encoded and gathered.hexdigest() != encoded['sha256']:
        errors.append(f"图像 {image['image_index']} rank{rank} 融合输入与编码输出原值不一致")
    origin = verify_feature(features, positions, encoder, image, rank, errors)
    return {'rank': rank, 'feature': origin, 'encoder_forward_ids': encoder, 'fusion_forward_ids': fusion}


def verify_feature(features, positions, encoder, image, rank, errors):
    origin = features[0] if features else {}
    desired = list(range(origin.get('offset', 0), origin.get('offset', 0) + origin.get('length', 0)))
    if origin.get('is_embed') is not None:
        desired = [p for p, use in zip(desired, origin['is_embed'], strict=True) if use]
    if len(encoder) != 1 or not positions or positions != desired or any(f != origin for f in features):
        errors.append(f"图像 {image['image_index']} rank{rank} 编码或占位覆盖不完整")
    return origin


def validate_grid(info):
    from math import prod
    grid, = info['grid_thw']
    feature = info['feature']
    count = sum(feature['is_embed']) if feature['is_embed'] is not None else feature['length']
    if len(grid) != 3 or any(type(x) != int or x < 1 for x in grid):
        raise ValueError('视觉输入网格无效')
    if (prod(grid) != info['pixel_shape'][0] or prod(grid) != count * info['spatial_merge_size'] ** 2
            or info['embedding_shape'][0] != count):
        raise ValueError('像素补丁数、空间合并、编码输出或占位数不匹配')
