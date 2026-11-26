"""普通注意力整批挂点，共用原始调用，只切观察参数。"""

from ..decode_cache.hooks import bind_attention
from ..decode_cache.prefill_attention import PrefillAttention
from .split import apply, extent, lengths


class AttentionBatch:
    def __init__(self, items, kernel, meta):
        self.items = items
        self.meta = meta
        for m in items:
            m.capture = PrefillAttention(m.store, m.group, m.step, kernel, meta.attn_state.name)
            m.event.update(m.capture.event)
            m.capture.event = m.event

    def before_write(self, args):
        slots = lengths(args['slot_mapping'])
        n = self.items[-1].end
        if slots[:n] != lengths(self.meta.slot_mapping[:n]) or len(set(slots[:n])) != n:
            raise ValueError('有效写槽与后端元数据不一致或成员写槽重叠')

        def write(m):
            actual = {k: m.segment(args[k]) for k in ('key', 'value', 'slot_mapping')}
            if lengths(actual['slot_mapping']) != m.event['expected_slots']:
                raise ValueError('成员写槽与调度不符')
            m.capture.before_write({**args, **actual})

        apply(self.items, write)

    def after_write(self):
        apply(self.items, lambda m: m.capture.after_write())

    def read(self, args):
        q, kv = map(lengths, (args['actual_seq_lengths'], args['actual_seq_lengths_kv']))

        def observe(m):
            i = m.index
            if q[i] != m.end:
                raise ValueError('FIA 累计 Token 边界与成员不符')
            table = args['block_table']
            local = {'actual_seq_lengths': [extent(q, i)], 'actual_seq_lengths_kv': [kv[i]]}
            if table is None:
                local.update(
                    key=m.segment(args['key']),
                    value=m.segment(args['value']),
                    actual_seq_lengths_kv=[extent(kv, i)],
                )
            else:
                local['block_table'] = table[i : i + 1]
            m.capture.read({**args, **local})
            if not m.event.get('read_plan_matches') or not all(
                m.event[k]['matches_input'] for k in ('key', 'value')
            ):
                raise ValueError('成员读页或 K/V 写后原值不符')

        apply(self.items, observe)


def bind(stack, rec, module, meta, items, ops, errors):
    if meta.num_actual_tokens != items[-1].end:
        raise ValueError('普通注意力的有效 Token 总数与 worker 不符')
    size = rec.runner.input_batch.block_table[items[0].group['group_id']].block_size
    bind_attention(stack, module.impl, AttentionBatch(items, size, meta), ops, errors)
