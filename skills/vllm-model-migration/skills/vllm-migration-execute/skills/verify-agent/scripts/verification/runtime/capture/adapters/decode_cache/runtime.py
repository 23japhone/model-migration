"""连接已启用的调度回执、真实后端实例、msProbe 前向帧和设备观测。"""
from contextlib import contextmanager, ExitStack
import hashlib
from importlib import import_module, metadata
from pathlib import Path

from .. import state
from ..parallel import request_folder, share
from .backends import BY_KIND, MLA
from .events import Attention, GDN, group_for
from .hooks import bind_attention, bind_gdn
from .layer_capture import install_layers, layer_errors
from .snapshots import observer_only, Snapshots, measure_observation


SOURCES = {
    'vllm_ascend.attention.attention_v1': '2b39ed5cf22b41640c4182129873613c4be7701e8084fcb8ed6077361b82704d',
    'vllm_ascend.ops.gdn': '8a655beb465863fe1858bbb28b0caf01ad5566f39179c17d53d934da991e5e42',
    'vllm_ascend.device.device_op': '0a6eb5da6c04b7f77dd343a66f2fa8ecdd03d040d1dbbf908ebd39b6c92c02d0',
}
BACKEND_SOURCES = {
    **SOURCES,
    'vllm_ascend.ops.mla': '9453b3a62904dfd0d696af019fe4fb97d24cc5a2a68c1a35946c636ed6159b53',
    'vllm_ascend.attention.mla_v1': 'fe9d7de289cbfa6429f9335fd3f164ebe74a7fc8695f9f95e81de1db152b0fea',
}
EXPECTED_OPS = ['npu_causal_conv1d_custom', 'npu_recurrent_gated_delta_rule', 'reshape_and_cache', 'fia']


def targets(model):
    modules = list(model.named_modules())
    (full_path, full), = [(n, m) for n, m in modules if ('.' + n).endswith('.layers.3.self_attn.attn')]
    (gdn_path, gdn), = [(n, m) for n, m in modules if ('.' + n).endswith('.layers.0.linear_attn')]
    return (full_path, full), (gdn_path, gdn)


def prepare_worker(rec, model):
    path = state.root() / 'decode-cache.json'
    if not path.is_file():
        return {}
    ready = {'status': 'unavailable'}
    try:
        ready['config_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        config = state.read(path)
        if config['status'] != 'available':
            raise ValueError(config.get('reason', '目标探针不可用'))
        from mindie_debugger.precision import MemoryWatchpoint
        if metadata.version('mindie-debugger') != config['version']:
            raise ValueError('目标 worker 的 mindie-debugger 版本变化')
        rec.cache_budget = share(rec, config['max_snapshot_bytes'])
        rec.cache_watch = MemoryWatchpoint
        if config.get('layer_scope', 'all') == 'all':
            return all_ready(rec, model, ready, config)
        layers = targets(model)
        classes = (type(layers[0][1].impl), type(layers[1][1]))
        actual = [c.__module__ + '.' + c.__name__ for c in classes]
        if actual != ['vllm_ascend.attention.attention_v1.AscendAttentionBackendImpl',
                      'vllm_ascend.ops.gdn.AscendGatedDeltaNetAttention']:
            raise ValueError('未经适配的注意力或 GDN 后端 ' + str(actual))
        for name, digest in SOURCES.items():
            if hashlib.sha256(Path(import_module(name).__file__).read_bytes()).hexdigest() != digest:
                raise ValueError('缓存挂点源码不符 ' + name)
        check_methods(layers[0][1].impl, ('reshape_and_cache', 'forward_fused_infer_attention'))
        check_methods(layers[1][1], ('_forward_core',))
        rec.cache_targets = layers
        rec.cache_budget = share(rec, config['max_snapshot_bytes'])
        rec.cache_watch = MemoryWatchpoint
        ready.update(status='ready', version=config['version'], sources=SOURCES,
                     max_snapshot_bytes=rec.cache_budget, layers=[layer[0] for layer in layers])
    except Exception as error:
        ready['reason'] = str(error)
    return {'decode_cache': ready}


def all_ready(rec, model, ready, config):
    rec.layer_modules = dict(model.named_modules())
    rec.backend_errors = {}
    verified_sources = {}
    for row in rec.architecture['layers']:
        for target in row['states']:
            try:
                sources = validate_backend(rec.layer_modules[target['module']], target['kind'],
                                           world_size=getattr(rec, 'world_size', 1))
                verified_sources[target['backend']] = sources
            except Exception as error:
                rec.backend_errors[target['module']] = f"{target['backend']}: {error}"
    ready.update(status='ready', version=config['version'], layer_scope='all', full_request=True,
                 layers=[r['path'] for r in rec.architecture['layers']], backend_errors=rec.backend_errors,
                 sources=verified_sources, max_snapshot_bytes=rec.cache_budget)
    return {'decode_cache': ready}


def validate_backend(module, kind, *, mode='eager', world_size=1):
    spec = BY_KIND.get(kind)
    if spec is None:
        raise ValueError('未知缓存后端 ' + kind)
    owner = module if spec.direct_address else module.impl
    if spec == MLA:
        if mode != 'eager' or world_size != 1:
            raise ValueError('MLA 缓存仅适配单卡 Eager')
        if owner.vllm_config.kv_transfer_config is not None:
            raise ValueError('MLA 状态观察要求本地缓存，当前配置启用 KV 传输')
        if owner.enable_kv_nz or owner.enable_mlapo or owner.fa_quant_layer:
            raise ValueError('MLA 缓存要求未量化 PA 布局和普通预处理路径')
    check_methods(owner, spec.methods)
    names = (spec.implementation.rsplit('.', 1)[0], *spec.dependencies)
    for name in names:
        if hashlib.sha256(Path(import_module(name).__file__).read_bytes()).hexdigest() != BACKEND_SOURCES[name]:
            raise ValueError('缓存后端源码与已核验实现不同 ' + name)
    return {name: BACKEND_SOURCES[name] for name in names}


def check_methods(owner, names):
    cls = type(owner)
    for name in names:
        method = getattr(owner, name).__func__
        if (method.__qualname__ != cls.__qualname__ + '.' + name
                or Path(method.__code__.co_filename).resolve() != Path(import_module(cls.__module__).__file__).resolve()):
            raise ValueError('缓存挂点实现与已核验源码不一致 ' + name)


def selected(rec, schedule):
    rec.cache_selection = None
    if hasattr(rec, 'cache_targets') or hasattr(rec, 'layer_modules'):
        intent = state.read(request_folder(rec.folder) / 'intent.json')
        index = intent.get('cache_output_index')
        if 'cache_capture' in intent:
            index = 'full_request'
            if getattr(rec, 'budget_folder', None) != rec.folder:
                rec.budget_folder = rec.folder
                rec.request_remaining = share(rec, intent['cache_capture']['max_request_snapshot_bytes'])
        if index is not None:
            rec.cache_selection = index, getattr(schedule, '_verify_cache_schedule_id', None)


def read_schedule(rec):
    frame = rec.frame
    identity = rec.cache_selection[1]
    if not identity:
        diagnostic = request_folder(rec.folder) / 'cache-schedule-error.json'
        raise ValueError(state.read(diagnostic)['error'] if diagnostic.exists() else '实际调度输出缺少缓存计划 ID')
    plan = state.read(request_folder(rec.folder) / ('schedule-' + identity + '.json'))
    if (plan['schedule_id'] != identity or plan['internal_id'] != frame['internal_id']
            or plan['output_index'] != frame['output_index'] or frame['positions'] != list(range(plan['position'], plan['position'] + plan.get('count', 1)))):
        raise ValueError('缓存计划与本次前向身份或输入位置不符')
    return plan


def preprocess_plan(runner):
    if runner.cache_config.mamba_cache_mode != 'align':
        return {'status': 'not_applicable'}
    buffers = runner._mamba_copy_bufs
    count = buffers.offset
    return {'status': 'runner_copy_plan', 'source_ptrs': buffers.src_ptrs.np[:count].tolist(),
            'destination_ptrs': buffers.dst_ptrs.np[:count].tolist(), 'bytes': buffers.sizes.np[:count].tolist()}


def install(stack, rec, record):
    from vllm.forward_context import get_forward_context
    plan = read_schedule(rec)
    record['schedule'] = plan
    runner = rec.runner
    if record.get('format') == 2:
        metas = get_forward_context().attn_metadata
        if any(s['kind'] == 'gdn' for row in rec.architecture['layers'] for s in row['states']):
            record['runner_preprocess'] = preprocess_plan(runner)
        install_layers(stack, rec, record, plan, metas)
        return
    (full_path, full), (gdn_path, gdn) = rec.cache_targets
    metas = get_forward_context().attn_metadata
    fa_meta, gdn_meta = metas[full.layer_name], metas[gdn.prefix]
    if (fa_meta.attn_state.name != 'DecodeOnly' or fa_meta.num_actual_tokens != 1
            or gdn_meta.num_decodes != 1 or gdn_meta.num_prefills or gdn_meta.num_spec_decodes):
        raise ValueError('本轮未命中普通注意力和 GDN 的有效纯 decode 路径')
    position = plan['position']
    group = group_for(plan, runner, full.layer_name)
    directory = rec.folder / ('cache-' + rec.frame['forward_id'])
    directory.mkdir(mode=0o700)
    snapshots = Snapshots(directory, rec.cache_budget, rec.cache_watch)
    attention = Attention(snapshots, group, position, runner.input_batch.block_table[group['group_id']].block_size)
    gdn_state = GDN(snapshots, group_for(plan, runner, gdn.prefix), position,
                    runner.cache_config.mamba_cache_mode, gdn_meta, gdn.kv_cache)
    attention.event['module'] = full_path
    gdn_state.event.update(module=gdn_path, observation_scope='layer_updates_after_runner_preprocess',
                           runner_preprocess=preprocess_plan(runner))
    record.update(attention=attention.event, gdn=gdn_state.event, snapshots=snapshots.files)
    bind_attention(stack, full.impl, attention, record['ops'], record['errors'])
    bind_gdn(stack, gdn, gdn_state, record['ops'], record['errors'])


@contextmanager
def observe_decode(rec):
    selection = getattr(rec, 'cache_selection', None)
    if selection is None or 'step' not in rec.frame or selection[0] not in ('full_request', rec.frame.get('output_index')):
        yield
        return
    frame = rec.frame
    record = {k: frame[k] for k in ('forward_id', 'internal_id', 'output_index', 'positions', 'step')}
    record.update(rank=getattr(rec, 'rank', 0), schedule_id=selection[1], ops=[], errors=[], status='incomplete')
    if hasattr(rec, 'layer_modules'):
        record.update(format=2, layers=[])
    frame['decode_cache'] = record
    with ExitStack() as stack:
        if selection[0] == 'full_request':
            stack.enter_context(measure_observation(record))
        try:
            with observer_only():
                install(stack, rec, record)
        except Exception as error:
            record['errors'].append(str(error))
        yield
    if record.get('format') == 2:
        for layer in record['layers']:
            problems = layer_errors(layer)
            layer['status'] = 'incomplete' if problems else 'complete'
            record['errors'].extend(f"{layer['path']}: {msg}" for msg in problems)
    elif record['ops'] != EXPECTED_OPS:
        record['errors'].append('层调用缺失、重复或顺序不同')
    if not record['errors']:
        record['status'] = 'complete'
