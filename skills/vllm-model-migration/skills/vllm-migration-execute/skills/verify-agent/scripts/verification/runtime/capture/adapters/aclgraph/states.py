"""从实际后端调用处取槽号和状态行；图内路径只安排设备操作。"""
from functools import wraps
from ..decode_cache.hooks import bind_method, Names


def install_state(stack, module, layer, descriptor, observer):
    if descriptor['kind'] == 'gdn':
        bind_gdn(module, layer, observer)
        return
    impl = module.impl
    for method in ('reshape_and_cache', 'do_kv_cache_update'):
        device = getattr(impl, method).__func__.__globals__['DeviceOperator']
        write = observe_write(device.reshape_and_cache, layer, observer)
        bind_method(stack, impl, method, 'DeviceOperator', Names(device, reshape_and_cache=write))
    from .read_plan import install_reads
    install_reads(impl, layer, observer)
    original = impl._get_fia_params

    @wraps(original)
    def fia(*args, **kwargs):
        result = original(*args, **kwargs)
        binding = observer.binding()
        if binding is not None:
            table = result[3]
            binding['read_sources'][layer] = 'input_kv' if table is None else 'paged_kv'
            if table is not None:
                observer.record(table[:1], layer, 'read.blocks')
        return result
    impl._get_fia_params = fia
    original_pa = impl.forward_paged_attention

    @wraps(original_pa)
    def paged(query, metadata, output=None):
        binding = observer.binding()
        if binding is not None:
            binding['read_sources'][layer] = 'paged_kv'
            observer.record(metadata.block_tables[:1], layer, 'read.blocks')
        return original_pa(query, metadata, output)
    impl.forward_paged_attention = paged


def observe_write(original, layer, observer):
    @wraps(original)
    def write(*args, **kw):
        binding = observer.binding()
        if binding is None:
            return original(*args, **kw)
        observer.cache_layers[kw['key_cache'].data_ptr()] = layer
        observer.block_sizes[layer] = kw['key_cache'].shape[1]
        binding.setdefault('kv_pointers', {})[layer] = {
            key: kw[key].data_ptr() for key in ('key', 'value', 'key_cache', 'value_cache')}
        slots = kw['slot_mapping'].reshape(-1)
        observer.record(slots, layer, 'write.slots')
        for name in ('key', 'value'):
            observer.record(kw[name], layer, name + '.input')
        result = original(*args, **kw)
        for name in ('key', 'value'):
            cache = kw[name + '_cache']
            # padding 槽可以为 -1；只读安全的地址，导出时裁掉 padding。
            safe = slots.long().clamp(min=0, max=cache.shape[0]*cache.shape[1]-1)
            observer.record(cache[safe // cache.shape[1], safe % cache.shape[1]], layer, name + '.after')
        return result
    return write


def bind_gdn(module, layer, observer):
    from .gdn import install_arguments
    install_arguments(module, layer, observer)
    original = module._forward_core
    context = original.__func__.__globals__['get_forward_context']

    @wraps(original)
    def core(*args, **kwargs):
        binding = observer.binding()
        if binding is None or context().attn_metadata is None:
            return original(*args, **kwargs)
        meta = context().attn_metadata[module.prefix]
        rows = meta.non_spec_state_indices_tensor.reshape(-1)[:1]
        observer.record(rows, layer, 'state.row')
        if meta.has_initial_state is not None:
            observer.record(meta.has_initial_state.reshape(-1)[:1], layer, 'state.initial')
        stores = list(zip(('conv', 'ssm'), module.kv_cache, strict=True))
        for name, cache in stores:
            observer.record(cache.index_select(0, rows.long().clamp(0, cache.shape[0]-1))[0], layer, name+'.before')
        result = original(*args, **kwargs)
        for name, cache in stores:
            observer.record(cache.index_select(0, rows.long().clamp(0, cache.shape[0]-1))[0], layer, name+'.after')
        return result
    module._forward_core = core
