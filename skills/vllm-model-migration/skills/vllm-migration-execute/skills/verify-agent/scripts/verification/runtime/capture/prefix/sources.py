"""只从同一活动会话中已封存的实际写入建立来源索引。"""
from collections import defaultdict

from ....evidence import read_bound
from ..adapters.state import read


def key(group, index, block):
    return group, index, block['epoch'], block['block'], block['generation']


def sources(folder, epoch, prompt, errors):
    index = defaultdict(list)
    lease = read(folder.parent / 'lease.json')
    for sealed in lease['sealed']:
        try:
            receipt = read_bound(sealed['receipt'])
            if not receipt.get('cache_capture', {}).get('prefix_reuse'):
                continue
            if receipt['request_id'] != sealed['trace_id']:
                raise ValueError('会话与生产回执的请求身份不同')
            actual = read_bound(receipt['engine_input'])['prompt_ids']
            for item in receipt['timeline']:
                if item['phase'] == 'prefill':
                    row = read_bound(item['row'])
                    if row['client_request_id'] != receipt['request_id'] or row['forward_id'] != item['forward_id']:
                        raise ValueError('生产前向与请求时间线身份不同')
                    row['_source_receipt'] = sealed['receipt']
                    add_rows(index, item, row, epoch, actual, prompt)
        except (KeyError, ValueError, OSError) as error:
            errors.append(f"{sealed['trace_id']} 来源回执: {error}")
    return index


def add_rows(index, item, row, epoch, actual, prompt):
    row['_source_row'] = item['row']
    cache = row['decode_cache']
    plan = cache.get('schedule', {})
    evidence = plan.get('prefix_cache', {})
    if evidence.get('epoch') != epoch or plan.get('forward_id') != row['forward_id']:
        return
    start, stop = item['position_range']
    for gid, blocks in enumerate(evidence['allocation']):
        page = plan['groups'][gid]['page_size']
        for logical in range(start // page, (stop - 1) // page + 1):
            block = blocks[logical]
            low, high = max(start, logical * page), min(stop, (logical + 1) * page)
            if block and actual[:high] == prompt[:high]:
                index[key(gid, logical, block)].append((low, high, row, block['hash']))


def spans(index, gid, blocks, page, count, recurrent):
    cursor = 0
    for logical, block in enumerate(blocks):
        if block is None:
            continue
        bound = (logical + 1) * page
        if recurrent and bound != count:
            continue
        writer_rows = index.get(key(gid, logical, block), [])
        for low, high, row, published_hash in writer_rows:
            if published_hash is not None and published_hash != block['hash']:
                continue
            if recurrent:
                if high != count:
                    continue
                low = 0
            if low != cursor or high > count:
                raise ValueError(f'位置 {cursor} 的来源前向不唯一或不连续')
            yield low, high, row, block
            cursor = high
    if cursor != count:
        raise ValueError(f'位置 [{cursor},{count}) 的生产请求来源未验证')
