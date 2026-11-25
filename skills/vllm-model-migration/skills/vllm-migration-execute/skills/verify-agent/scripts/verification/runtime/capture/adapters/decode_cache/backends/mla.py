"""在 MLA 融合写入和实际注意力调用两侧保留单请求原始证据。"""
from functools import wraps

from .mla_evidence import verify_mla, verify_components
from ..events import one
from ..hooks import Names, bind_method
from ..prefill_attention import write_slots
from ..snapshots import fence, layout, observer_only


def instrument(stack, owner, method, namespace, op, state, label, before, after=None):
    obj = getattr(owner, method).__func__.__globals__[namespace]
    original = getattr(obj, op)

    @wraps(original)
    def wrapped(*args, **kwargs):
        state['ops'].append(label)
        try:
            with observer_only():
                before(args, kwargs)
        except Exception as error:
            state['errors'].append(f'{op} before: {error}')
            return original(*args, **kwargs)
        result = original(*args, **kwargs)
        if after:
            try:
                with observer_only():
                    after(result)
            except Exception as error:
                state['errors'].append(f'{op} after: {error}')
        return result
    bind_method(stack, owner, method, namespace, Names(obj, **{op: wrapped}))


def blocks_for(table, end, kernel):
    count = (end + kernel - 1) // kernel
    if table.ndim != 2 or table.shape[0] != 1 or table.shape[1] < count:
        raise ValueError('MLA 有效块表形状不符')
    return table[0, :count].tolist()


class MLAObservation:
    def __init__(self, store, group, schedule, module, meta, errors):
        self.errors = errors
        self.store = store
        self.count = schedule.get('count', 1)
        self.start = schedule['position']
        self.caches = module.kv_cache
        self.kernel = self.caches[0].shape[1]
        self.widths = (module.impl.kv_lora_rank, module.impl.qk_rope_head_dim)
        self.prefill = (meta.num_decodes, meta.num_prefills) == (0, 1)
        if meta.num_actual_tokens != self.count or self.kernel != 128:
            raise ValueError('MLA 有效 Token 数或物理块长不符')
        if not self.prefill and ((meta.num_decodes, meta.num_prefills) != (1, 0) or self.count != 1):
            raise ValueError('MLA 要求单请求有效后端分支')
        self.event = {**group, 'observation': 'mla', 'kernel_block_size': self.kernel,
                      'backend_path': 'prefill' if self.prefill else 'decode', 'history': [],
                      'component_dims': list(self.widths),
                      'expected_slots': write_slots(group, self.kernel, self.start, self.count)}
        self.expected_meta_slots = meta.slot_mapping[:self.count].tolist()

    def check_components(self, latent, rope):
        for actual, bound, width in zip((latent, rope), self.caches, self.widths, strict=True):
            if (actual.data_ptr() != bound.data_ptr() or not actual.is_contiguous()
                    or str(actual.dtype) != 'torch.bfloat16' or actual.shape[-1] != width
                    or actual.numel() != bound.numel()):
                raise ValueError('MLA 算子未绑定本层预期的物理分量或布局')

    def write(self, args, kw):
        projection, _, _, _, slots, rope, latent = args
        self.check_components(latent, rope)
        ids = slots.tolist()
        if (kw['cache_mode'] != 'PA' or kw.get('c_kv_scale') is not None
                or bool(kw.get('is_output_kv')) != self.prefill or latent.shape[1:3] != (self.kernel, 1)
                or rope.shape[:-1] != latent.shape[:-1] or projection.shape != (self.count, 1, 1, sum(self.widths))):
            raise ValueError('MLA 融合写入格式不符')
        if len(ids) != self.count or len(set(ids)) != self.count or min(ids) < 0 or max(ids) >= latent.shape[0]*self.kernel:
            raise ValueError('MLA 融合写槽数量或有效范围异常')
        self.slots = slots
        self.event.update(write_slots=ids, metadata_slots=self.expected_meta_slots,
                          cache_layouts=[layout(latent), layout(rope)])
        self.snapshot('mla.projection.input', projection)

    def snapshot(self, name, tensor):
        try:
            self.store.boundary(name, tensor)
        except Exception as error:
            self.errors.append(f'{name}: {error}')

    def after(self, outputs):
        for index, (name, cache) in enumerate(zip(('latent', 'rope'), self.caches, strict=True)):
            try:
                fence(cache)
                actual = cache.flatten(0, 1).index_select(0, self.slots)
                self.snapshot('mla.' + name + '.after', actual)
                if self.prefill:
                    self.snapshot('mla.' + name + '.output', outputs[3-index].reshape_as(actual))
            except Exception as error:
                self.errors.append(f'mla.{name}: {error}')

    def current(self, args, kw):
        self.event['current'] = {'source': 'projected_current_input', 'query_length': one(kw['actual_seq_lengths']),
                                 'kv_length': one(kw['actual_seq_lengths_kv']), 'layout': kw['input_layout'],
                                 'sparse_mode': kw['sparse_mode'], 'has_mask': kw['atten_mask'] is not None,
                                 'has_table': kw['block_table'] is not None}

    def load(self, args, kw):
        latent, rope, table, lengths, starts = args
        self.check_components(latent, rope)
        start, length = one(starts), one(lengths)
        if [list(kw[k].shape) for k in ('key', 'value')] != [[length, 1, w] for w in self.widths]:
            raise ValueError('MLA 历史加载目的分量不符')
        self.event['history'].append({'start': start, 'length': length,
                                      'blocks': blocks_for(table, start + length, self.kernel)})

    def history_attention(self, args, kw):
        chunk = self.event['history'][-1]
        chunk.update(fia_length=one(kw['actual_seq_lengths_kv']), query_length=one(kw['actual_seq_lengths']),
                     sparse_mode=kw['sparse_mode'], has_mask=kw['atten_mask'] is not None, layout=kw['input_layout'])

    def decode(self, args, kw):
        _, key, value = args
        self.check_components(key, kw['key_rope'])
        if layout(key) != layout(value) or key.shape[1:3] != (1, self.kernel):
            raise ValueError('MLA decode K/V 未共用本层 latent 缓存')
        length = one(kw['actual_seq_kvlen'])
        self.event['decode'] = {'source': 'paged_latent_and_rope', 'kv_length': length, 'layout': kw['input_layout'],
                                'sparse_mode': kw['sparse_mode'], 'block_size': kw['block_size'],
                                'has_mask': kw['atten_mask'] is not None,
                                'blocks': blocks_for(kw['block_table'], length, self.kernel)}


def bind_mla(stack, module, store, group, plan, meta, result):
    capture = MLAObservation(store, group, plan, module, meta, result['errors'])
    impl = module.impl
    method = 'exec_kv_prefill' if capture.prefill else 'exec_kv_decode'
    instrument(stack, impl, method, 'torch_npu', 'npu_kv_rmsnorm_rope_cache',
               result, 'mla.write', capture.write, capture.after)
    if capture.prefill:
        op = 'npu_fused_infer_attention_score'
        instrument(stack, impl, '_forward_prefill', 'torch_npu', op, result, 'mla.current', capture.current)
        instrument(stack, impl, '_compute_prefill_context', 'DeviceOperator', 'kv_cache_load',
                   result, 'mla.load', capture.load)
        instrument(stack, impl, '_compute_prefill_context', 'torch_npu', op, result, 'mla.history', capture.history_attention)
    else:
        instrument(stack, impl, '_forward_decode', 'torch_npu', 'npu_fused_infer_attention_score_v2',
                   result, 'mla.decode', capture.decode)
    def finish():
        try:
            verify_mla(capture.event, group, plan)
            verify_components(capture.event, store.files, capture.count)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            result['errors'].append(f'MLA {group["layer"]}: {error}')

    stack.callback(finish)
    return capture
