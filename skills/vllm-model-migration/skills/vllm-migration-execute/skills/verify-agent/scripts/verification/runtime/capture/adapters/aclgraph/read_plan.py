"""记录传给 FIA / PA 的实参，等图参数更新结束后再读取设备张量。"""
from functools import wraps
from types import FunctionType
from ..decode_cache.hooks import Names, bind_method
from .replay import active


def native_call(original, observer, layer=None):
    @wraps(original)
    def called(*args, **kwargs):
        result = original(*args, **kwargs)
        if active.get():
            observed = dict(kwargs)
            for name, arg in zip(('query', 'key', 'value'), args):
                observed.setdefault(name, arg)
            observer.read_calls.append((layer, observed))
        return result
    return called


def install_reads(impl, layer, observer):
    method = impl.forward_fused_infer_attention.__func__
    npu = method.__globals__['torch_npu']
    fused = {name: native_call(getattr(npu, name), observer, layer) for name in (
        'npu_fused_infer_attention_score', 'npu_fused_infer_attention_score_v2')}
    bind_method(observer.scopes, impl, 'forward_fused_infer_attention', 'torch_npu',
                Names(npu, **fused))
    paged = native_call(npu._npu_paged_attention, observer, layer)
    bind_method(observer.scopes, impl, 'forward_paged_attention', 'torch_npu', Names(npu, _npu_paged_attention=paged))
    cls = type(impl)
    if cls in observer.updated_types:
        return
    observer.updated_types.add(cls)
    fn = cls.update_graph_params
    replacements = {name: Names(getattr(npu, name), out=native_call(getattr(npu, name).out, observer))
                    for name in ('npu_fused_infer_attention_score', 'npu_fused_infer_attention_score_v2')}
    local = Names(npu, _npu_paged_attention=native_call(npu._npu_paged_attention, observer), **replacements)
    updated = FunctionType(fn.__code__, {**fn.__globals__, 'torch_npu': local}, fn.__name__, fn.__defaults__, fn.__closure__)
    updated.__kwdefaults__ = fn.__kwdefaults__
    cls.update_graph_params = staticmethod(updated)


def first(value):
    if hasattr(value, 'tolist'):
        value = value.tolist()
    return int(value[0])


def materialize(observer, frame):
    plans = {}
    bindings = [observer.direct, *(b for _, b in observer.pending)]
    for index, args in observer.read_calls:
        if index is None:
            tensor = args['key'] if 'key' in args else args['key_cache']
            index = observer.cache_layers.get(tensor.data_ptr())
        if index is None:
            frame['errors'].append('无法将图参数更新的 K 缓存绑定到层')
            continue
        try:
            sources = [b['kv_pointers'][index] for b in bindings if index in b.get('kv_pointers', {})]
            source, = sources
            if str(index) in plans:
                raise ValueError('同一层出现多次注意力读取')
            plans[str(index)] = backend_plan(args, source, observer.block_sizes[index])
        except (ValueError, KeyError, IndexError, RuntimeError, StopIteration) as error:
            frame['errors'].append(f'{index}/注意力实参 {error}')
    return plans


def length_arg(args, names, default=None):
    for name in names:
        if name in args:
            return first(args[name])
    if default is None:
        raise ValueError('读取调用缺少有效长度')
    return default


def backend_plan(args, pointers, kernel):
    table = args['block_table']
    paged = table is not None
    for name in ('key', 'value'):
        argument = args[name] if name in args else args[name+'_cache']
        if argument.data_ptr() != pointers[name+'_cache' if paged else name]:
            raise ValueError('实际 K/V 地址与本层的缓存或本轮输入不符')
    length = length_arg(args, ('context_lens', 'actual_seq_lengths_kv', 'actual_seq_kvlen'))
    block = args.get('block_size') or kernel
    if paged and (block != kernel or table.ndim != 2):
        raise ValueError('块大小或读取页表维数与物理缓存不符')
    return {'source': 'paged_kv' if paged else 'input_kv', 'kv_length': length, 'block_size': kernel,
            'blocks': [] if table is None else table[0, :(length+block-1)//block].tolist(),
            'query_length': length_arg(args, ('actual_seq_lengths', 'actual_seq_qlen'), default=1),
            'sparse_mode': args.get('sparse_mode'), 'input_layout': args.get('input_layout'),
            'addresses': pointers, 'backend': 'PA' if 'context_lens' in args else 'FIA'}
