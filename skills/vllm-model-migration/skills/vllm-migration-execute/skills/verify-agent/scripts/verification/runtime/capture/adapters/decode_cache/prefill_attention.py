"""在有效输入行上观察整块 K/V 写入和 FIA 实际读取来源。"""
from .events import one
from .plan import attention_plan, slot_ranges
from .snapshots import layout, fence


def write_slots(group, kernel, start, count):
    page = group['page_size']
    blocks = group['blocks']
    if page < kernel or page % kernel or start < 0 or count < 1:
        raise ValueError('prefill 页大小或位置无效')
    return [blocks[p // page] * page + p % page for p in range(start, start + count)]


class PrefillAttention:
    def __init__(self, store, group, schedule, kernel, backend_state):
        self.store = store
        self.kernel = kernel
        self.count = schedule['count']
        self.start = schedule['position']
        expected = attention_plan(group['blocks'], group['page_size'], kernel, self.start + self.count - 1)
        self.event = {**group, 'backend_state': backend_state, 'kernel_block_size': kernel,
                      'expected_slots': write_slots(group, kernel, self.start, self.count),
                      'expected_read_blocks': expected['expected_read_blocks'], 'observation': 'prefill_kv'}
        self.buffers = {}

    def before_write(self, args):
        slots = args['slot_mapping'].tolist()
        if len(slots) != self.count or len(set(slots)) != self.count or min(slots) < 0:
            raise ValueError('有效写槽数量、重复或填充位置异常')
        self.event.update(write_slots=slots, write_plan_matches=slots == self.event['expected_slots'])
        for name in ('key', 'value'):
            incoming, cache = args[name], args[name + '_cache']
            if (cache.ndim != 4 or cache.shape[1] != self.kernel or max(slots) >= cache.shape[0]*self.kernel
                    or incoming.shape != (self.count, *cache.shape[2:]) or incoming.dtype != cache.dtype):
                raise ValueError('有效 K/V 输入或物理缓存布局不符')
            self.buffers[name] = incoming, cache
            self.store.save(name + '.input', incoming)

    def after_write(self):
        import torch
        for name, (incoming, cache) in self.buffers.items():
            fence(cache)
            slots = torch.tensor(self.event['write_slots'], device=cache.device, dtype=torch.long)
            actual = cache[slots // self.kernel, slots % self.kernel]
            self.store.boundary(name + '.after', actual)
            matches = (actual.view(torch.uint8) == incoming.contiguous().view(torch.uint8)).reshape(self.count, -1).all(1)
            self.event[name] = {'matches_by_position': matches.tolist(), 'matches_input': bool(matches.all().item())}

    def read(self, args):
        q, kv = one(args['actual_seq_lengths']), one(args['actual_seq_lengths_kv'])
        if q != self.count or kv != self.start + q or args['sparse_mode'] != 3 or args['input_layout'] != 'TND':
            raise ValueError('FIA 有效因果区间与请求不符')
        self.event.update(query_length=q, kv_length=kv, sparse_mode=3, input_layout='TND',
                          causal_end_exclusive=list(range(self.start + 1, kv + 1)))
        table = args['block_table']
        if table is None:
            if self.start or self.event['backend_state'] != 'PrefillNoCache':
                raise ValueError('已有前文但 FIA 未绑定缓存块表')
            for name, (incoming, _) in self.buffers.items():
                if layout(args[name]) != layout(incoming):
                    raise ValueError('冷 prefill 的 FIA 输入不是本轮 K/V')
            self.event.update(read_source='input_kv', read_plan_matches=True)
            return
        if args['block_size'] != self.kernel or table.ndim != 2 or table.shape[0] != 1:
            raise ValueError('FIA 缓存块表布局不符')
        blocks = table[0, :(kv + self.kernel - 1)//self.kernel].tolist()
        for name, (_, cache) in self.buffers.items():
            expected = cache.view(cache.shape[0], self.kernel, -1)
            if layout(args[name]) != layout(expected):
                raise ValueError('FIA 未读取本层绑定的物理缓存')
        self.event.update(read_source='paged_kv', read_blocks=blocks,
                          read_slot_ranges=slot_ranges(blocks, self.kernel, kv),
                          read_plan_matches=blocks == self.event['expected_read_blocks'])
