"""Observe the V1 encoder, encoder cache and actual Qwen image embedding merge."""
from functools import wraps
from pathlib import Path
import hashlib
from time import perf_counter
from importlib import import_module

from .decode_cache.layer_capture import floating_tensor
from .decode_cache.snapshots import Snapshots, observer_only


def feature_info(feature):
    span = feature.mm_position
    return {'identifier': feature.identifier, 'mm_hash': feature.mm_hash, 'image_index': 0,
            'offset': span.offset, 'length': span.length,
            'is_embed': span.is_embed.tolist() if span.is_embed is not None else None}


def initialize(rec, schedule):
    features = rec.runner.requests[rec.frame['internal_id']].mm_features
    if not features:
        return
    if rec.frame.get('profile') == 'tensor_alignment':
        raise ValueError('定向张量采集仅支持文本')
    if len(features) != 1 or features[0].modality != 'image':
        raise ValueError('图文采集仅支持单张图片')
    rec.start_probe()
    frame = rec.frame
    scheduled = schedule.scheduled_encoder_inputs.get(frame['internal_id'], [])
    frame['vision'] = {'feature': feature_info(features[0]), 'scheduled': list(scheduled), 'snapshots': {},
                       'spatial_merge_size': rec.runner.model_config.hf_config.vision_config.spatial_merge_size,
                       'encoder_executed': False, 'fusion_positions': [], 'snapshot_bytes': 0, 'observer_seconds': 0.0}
    if scheduled and scheduled != [0]:
        raise ValueError('图像调度项不匹配')


def save(rec, name, tensor):
    if not rec.cache_selection:
        return
    info = rec.frame['vision']
    directory = rec.folder / ('vision-' + rec.frame['forward_id'])
    directory.mkdir(mode=0o700, exist_ok=True)
    remaining = min(rec.request_remaining, rec.cache_budget - info['snapshot_bytes'])
    snap = Snapshots(directory, remaining, rec.cache_watch)
    path = directory / (name + '.bin')
    if path.exists():
        raise ValueError('视觉快照名称重复 ' + name)
    started = perf_counter()
    try:
        with observer_only():
            snap.boundary(name, tensor)
    finally:
        info['observer_seconds'] += perf_counter() - started
        if path.is_file():
            size = path.stat().st_size
            rec.request_remaining -= size
            info['snapshot_bytes'] += size
    info['snapshots'][name] = snap.files[name]


def fusion(rec, args, kwargs, result):
    import torch
    info = rec.frame['vision']
    with observer_only():
        mask = kwargs['is_multimodal']
        absolute = rec.runner._positions_np_buf[:rec.frame['count']].tolist()
        info.update(mask=mask.tolist(), fusion_positions=[p for p, used in zip(absolute, mask.tolist(), strict=True) if used])
        embeds = kwargs['multimodal_embeddings']
        if not embeds:
            return
        actual = result[mask.to(result.device)]
        source, = embeds
        info['fusion_equal'] = torch.equal(source, actual)
        save(rec, 'fusion.source', source)
        save(rec, 'fusion.result', actual)


def bind(rec, obj, method, after, before=None):
    original = getattr(obj, method)
    @wraps(original)
    def observed(*args, **kwargs):
        active = rec.frame is not None and 'vision' in rec.frame
        if active and before:
            rec.observe(before, args, kwargs)
        result = original(*args, **kwargs)
        if active:
            rec.observe(after, args, kwargs, result)
        return result
    setattr(obj, method, observed)


def install(rec, model):
    runner = rec.runner
    if rec.world_size <= 1 or 'visual.blocks' not in rec.architecture.get('other_stacks', {}):
        return
    # Install the adapter only on a model whose merge and encoder implementations were inspected.
    if type(model).__module__ != 'vllm.model_executor.models.qwen3_5':
        return
    for name, digest in {
        'vllm.model_executor.models.qwen3_5': '95013496de9ca5357acd1e72f35347ed8d8d6b2b4af7ea1cb907a1afeebe05e5',
        'vllm.model_executor.models.qwen3_vl': 'f6dd717d4e0a428ca773346ec721880acd5111dd09b38756caad0c84bf1afbac',
        'vllm.v1.worker.gpu_model_runner': 'd71f4eb4547717a26a893d46d1dcb877731f9f5cba1de2f65376d24a6f95221f',
    }.items():
        if hashlib.sha256(Path(import_module(name).__file__).read_bytes()).hexdigest() != digest:
            raise ValueError('图文采集源码版本未适配 ' + name)
    original = runner._execute_mm_encoder
    @wraps(original)
    def encode(scheduler_output):
        if rec.frame:
            rec.observe(initialize, rec, scheduler_output)
        return original(scheduler_output)
    runner._execute_mm_encoder = encode
    def inputs(args, kwargs):
        info = rec.frame['vision']
        info['encoder_executed'] = True
        with observer_only():
            info['grid_thw'] = kwargs['image_grid_thw'].tolist()
            info['pixel_shape'] = list(kwargs['pixel_values'].shape)
        save(rec, 'pixels', kwargs['pixel_values'])
        save(rec, 'grid', kwargs['image_grid_thw'])
    def output(args, kwargs, result):
        tensor, = result
        rec.frame['vision']['embedding_shape'] = list(tensor.shape)
        save(rec, 'embedding', tensor)
    bind(rec, model, 'embed_multimodal', output, inputs)
    bind(rec, model, 'embed_input_ids', lambda a, k, out: fusion(rec, a, k, out))
    bind_layers(rec, model)


def bind_layers(rec, model):
    blocks = rec.architecture['other_stacks']['visual.blocks']
    if len(blocks) != rec.runner.model_config.hf_config.vision_config.depth:
        raise ValueError('视觉层数与已加载视觉栈不符')
    modules = dict(model.named_modules())
    for name in ['visual.patch_embed', *(item['path'] for item in blocks), 'visual.merger']:
        def before(args, kwargs, name=name):
            if rec.cache_selection:
                save(rec, name + '.input', floating_tensor((args, kwargs)))
        def after(args, kwargs, result, name=name):
            if rec.cache_selection:
                save(rec, name + '.output', floating_tensor(result))
        bind(rec, modules[name], 'forward', after, before)
