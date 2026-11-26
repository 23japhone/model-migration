"""按实际状态索引及变长序列边界拆卷积、chunk 和 recurrent 调用。"""

from types import SimpleNamespace
from ..decode_cache.events import GDN
from ..decode_cache.hooks import bind_gdn
from ..decode_cache.prefill_gdn import PrefillGDN, bind_prefill_gdn
from .split import apply, lengths


class GatedBatch:
    def __init__(self, rec, module, meta, items, chunk):
        self.items = items
        self.offsets = [0, *(m.end for m in items)]
        self.final = None
        ids = meta.non_spec_state_indices_tensor.reshape(-1)[: len(items)]
        if len(set(lengths(ids))) != len(items):
            raise ValueError('GDN 状态行重叠或缺失')
        for m in items:
            i = m.index
            view = SimpleNamespace(non_spec_state_indices_tensor=ids[i : i + 1])
            mode = rec.runner.cache_config.mamba_cache_mode
            if chunk:
                view.has_initial_state = meta.has_initial_state[i : i + 1]
                cap = PrefillGDN(m.store, m.group, m.step, mode, view, module.kv_cache)
                if cap.initial != (m.step['position'] > 0):
                    m.errors.append('GDN 有效初态与本成员冷启动或续块位置不符')
            else:
                cap = GDN(m.store, m.group, m.step['position'], mode, view, module.kv_cache)
                cap.event.update(granularity='forward_token', initial_source='cache')
            m.capture = cap
            m.event.update(cap.event, backend_path='chunk' if chunk else 'recurrent')
            cap.event = m.event

    def local(self, m, args, offsets, indices):
        expected = (
            self.offsets
            if offsets != 'actual_seq_lengths'
            else [0, *(item.end - item.begin for item in self.items)]
        )
        if lengths(args[offsets]) != expected:
            raise ValueError('GDN 算子 Token 边界与 worker 不符')
        i = m.index
        row = args[indices][i : i + 1]
        if lengths(row) != [m.event['expected_row']] or m.event['metadata_row'] != m.event['expected_row']:
            raise ValueError('GDN 实际状态行与本请求分配不符')
        return {**args, offsets: [0, m.end - m.begin], indices: row}

    def before_conv(self, args):
        def collect(m):
            local = self.local(m, args, 'query_start_loc_opt', 'cache_indices_opt')
            local['initial_state_mode_opt'] = args['initial_state_mode_opt'][m.index : m.index + 1]
            m.capture.before_conv(local)

        apply(self.items, collect)

    def before_chunk(self, args):
        if lengths(args['cu_seqlens']) != self.offsets:
            raise ValueError('递归块 Token 区间与成员不符')

        def collect(m):
            if m.event['metadata_row'] != m.event['expected_row']:
                raise ValueError('递归状态行与分配不同')
            m.capture.before_chunk(
                {
                    **args,
                    'cu_seqlens': args['cu_seqlens'].new_tensor([0, m.end - m.begin]),
                    'initial_state': args['initial_state'][m.index : m.index + 1],
                }
            )

        apply(self.items, collect)

    def before(self, name, args):
        def collect(m):
            if name == 'conv':
                local = self.local(m, args, 'query_start_loc_opt', 'cache_indices_opt')
            else:
                local = self.local(m, args, 'actual_seq_lengths', 'ssm_state_indices')
                local['actual_seq_lengths'] = args['actual_seq_lengths'].new_tensor(local['actual_seq_lengths'])
            m.capture.before(name, local)

        apply(self.items, collect)

    def after(self, name):
        apply(self.items, lambda m: m.capture.after(name))

    def writeback(self):
        def collect(m):
            m.capture.final = self.final[m.index : m.index + 1]
            m.capture.writeback()

        apply(self.items, collect)


def bind(stack, rec, module, meta, items, ops, errors):
    if meta.num_spec_decodes or meta.num_prefills + meta.num_decodes != len(items):
        raise ValueError('GDN 有效序列或推测解码分组不符')
    chunk = bool(meta.num_prefills)
    capture = GatedBatch(rec, module, meta, items, chunk)
    binder = bind_prefill_gdn if chunk else bind_gdn
    binder(stack, module, capture, ops, errors)
