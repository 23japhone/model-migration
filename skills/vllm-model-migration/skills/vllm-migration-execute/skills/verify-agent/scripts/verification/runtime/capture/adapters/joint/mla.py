"""从 MLA 后端实际的 decode/prefill 子调用分别取得成员视图。"""

from types import SimpleNamespace
from ..decode_cache.backends.mla import MLAObservation, instrument
from ..decode_cache.backends.mla_evidence import verify_mla, verify_components
from .split import apply, extent, lengths


class Partition:
    def __init__(self, items, prefill, module, meta):
        self.items = items
        self.base = items[0].begin
        self.prefill = prefill
        self.active_history = set()
        for m in items:
            single = SimpleNamespace(
                num_decodes=int(not prefill),
                num_prefills=int(prefill),
                num_actual_tokens=m.end - m.begin,
                slot_mapping=m.segment(meta.slot_mapping),
            )
            m.record['ops'] = []
            cap = MLAObservation(m.store, m.group, m.step, module, single, m.errors)
            m.capture = cap
            m.event.update(cap.event)
            cap.event = m.event

    def write(self, args, kw):
        def collect(m):
            lo, hi = m.begin - self.base, m.end - self.base
            local = list(args)
            local[0], local[4] = args[0][lo:hi], args[4][lo:hi]
            if lengths(local[4]) != m.event['expected_slots'] or lengths(local[4]) != m.capture.expected_meta_slots:
                raise ValueError('MLA 实际写槽与成员分配不符')
            m.record['ops'].append('mla.write')
            m.capture.write(local, kw)

        apply(self.items, collect)

    def after(self, outputs):
        def collect(m):
            local = list(outputs)
            if self.prefill:
                for i in (2, 3):
                    local[i] = local[i][m.begin - self.base : m.end - self.base]
            m.capture.after(local)

        apply(self.items, collect)

    def local_read(self, m, kw, current):
        i = m.index - self.items[0].index
        q = lengths(kw['actual_seq_lengths'])
        if q[i] != m.end - self.base:
            raise ValueError('MLA 子调用查询边界不符')
        local = {
            **kw,
            'actual_seq_lengths': [extent(q, i)],
            'actual_seq_lengths_kv': [extent(lengths(kw['actual_seq_lengths_kv']), i)],
        }
        if current:
            m.record['ops'].append('mla.current')
            m.capture.current((), local)
        elif m.index in self.active_history:
            m.record['ops'].append('mla.history')
            m.capture.history_attention((), local)

    def current(self, args, kw):
        apply(self.items, lambda m: self.local_read(m, kw, True))

    def history(self, args, kw):
        apply(self.items, lambda m: self.local_read(m, kw, False))

    def load(self, args, kw):
        latent, rope, table, counts, starts = args
        counts_list = lengths(counts)
        self.active_history.clear()

        def collect(m):
            i = m.index - self.items[0].index
            n = counts_list[i]
            if n:
                lo = sum(counts_list[:i])
                local = {k: kw[k][lo : lo + n] for k in ('key', 'value')}
                m.record['ops'].append('mla.load')
                m.capture.load((latent, rope, table[i : i + 1], counts[i : i + 1], starts[i : i + 1]), local)
                self.active_history.add(m.index)

        apply(self.items, collect)

    def decode(self, args, kw):
        def collect(m):
            i = m.index
            local = {
                **kw,
                'actual_seq_kvlen': kw['actual_seq_kvlen'][i : i + 1],
                'block_table': kw['block_table'][i : i + 1],
            }
            m.record['ops'].append('mla.decode')
            m.capture.decode(args, local)

        apply(self.items, collect)


def attach(stack, impl, part, result):
    method = 'exec_kv_prefill' if part.prefill else 'exec_kv_decode'
    instrument(
        stack, impl, method, 'torch_npu', 'npu_kv_rmsnorm_rope_cache', result, 'mla.write', part.write, part.after
    )
    if part.prefill:
        op = 'npu_fused_infer_attention_score'
        instrument(stack, impl, '_forward_prefill', 'torch_npu', op, result, 'mla.current', part.current)
        instrument(
            stack,
            impl,
            '_compute_prefill_context',
            'DeviceOperator',
            'kv_cache_load',
            result,
            'mla.load',
            part.load,
        )
        instrument(stack, impl, '_compute_prefill_context', 'torch_npu', op, result, 'mla.history', part.history)
    else:
        instrument(
            stack,
            impl,
            '_forward_decode',
            'torch_npu',
            'npu_fused_infer_attention_score_v2',
            result,
            'mla.decode',
            part.decode,
        )


def bind(stack, rec, module, meta, items, ops, errors):
    total = items[-1].end
    slots = lengths(meta.slot_mapping[:total])
    if meta.num_actual_tokens != total or len(set(slots)) != total or min(slots) < 0:
        raise ValueError('MLA 的有效 Token 数或跨成员写槽不符')
    n = meta.num_decodes
    if n + meta.num_prefills != len(items) or sum(m.end - m.begin for m in items[:n]) != meta.num_decode_tokens:
        raise ValueError('MLA 重排后子批次的有效边界不符')
    for subset, prefill in ((items[:n], False), (items[n:], True)):
        if subset:
            attach(stack, module.impl, Partition(subset, prefill, module, meta), {'ops': ops, 'errors': errors})

    def verify(m):
        verify_mla(m.event, m.group, m.step)
        verify_components(m.event, m.store.files, m.step['count'])

    stack.callback(apply, items, verify)
