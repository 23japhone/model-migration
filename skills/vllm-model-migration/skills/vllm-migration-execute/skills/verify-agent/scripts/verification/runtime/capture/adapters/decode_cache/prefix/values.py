"""从实际绑定的分页缓存和 GDN 状态行抽取首次复用的有效值。"""
from ..snapshots import fence


def first_hit(plan):
    return plan.get('prefix_cache', {}).get('lookup', {}).get('length', 0)


def save_pages(store, plan, group, caches, names, errors):
    count = first_hit(plan)
    if not count:
        return
    page = group['page_size']
    for name, cache in zip(names, caches, strict=True):
        try:
            import torch
            fence(cache)
            kernel = cache.shape[1]
            if page % kernel or count % page:
                raise ValueError('前缀长度或内核页未按缓存组对齐')
            if count * cache[0, 0].numel() * cache.element_size() > store.budget.remaining:
                raise ValueError('前缀有效行超过本次剩余快照预算')
            blocks = group['blocks'][:count // page]
            pages = [block * (page // kernel) + i for block in blocks for i in range(page // kernel)]
            index = torch.tensor(pages, device=cache.device, dtype=torch.long)
            store.boundary(name + '.prefix', cache.index_select(0, index).flatten(0, 1))
        except Exception as error:
            errors.append(f'{name}.prefix: {error}')


def gdn_routes(rec, plan, group, capture, caches):
    hit = first_hit(plan)
    if not hit:
        return
    selected = plan['prefix_cache']['lookup']['groups'][group['group_id']]
    block = selected[(hit - 1) // group['page_size']]['block']
    target = capture.event['metadata_row']
    routes = {}
    capture.event['prefix_state'] = routes
    for name, cache in zip(('conv', 'ssm'), caches, strict=True):
        fence(cache)
        src, dst = cache[block], cache[target]
        size = src.numel() * src.element_size()
        ranges = []
        if block != target:
            buf = rec.runner._mamba_copy_bufs
            for a, b, n in zip(buf.src_ptrs.np[:buf.offset], buf.dst_ptrs.np[:buf.offset], buf.sizes.np[:buf.offset]):
                a, b, n = int(a) - src.data_ptr(), int(b) - dst.data_ptr(), int(n)
                if 0 <= a < size and 0 <= b < size and 0 < n <= min(size - a, size - b):
                    ranges.append([a, b, n])
            if not ranges:
                raise ValueError(f'{name} 跨行恢复缺少 runner 复制记录')
        routes[name] = {'source_row': block, 'target_row': target,
                        'copy_ranges': ranges if block != target else [[0, 0, size]]}
