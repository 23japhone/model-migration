"""独立按调度页、实际算子参数和原始文件核对 MLA 层。"""
from ..plan import attention_plan
from ..prefill_attention import write_slots


def operations(event):
    if event.get('backend_path') == 'decode':
        return ['mla.write', 'mla.decode']
    return ['mla.write', 'mla.current'] + ['mla.load', 'mla.history'] * len(event.get('history', ()))


def verify_reads(event, group, start, count):
    kernel = event['kernel_block_size']

    def blocks(end):
        return attention_plan(group['blocks'], group['page_size'], kernel, end - 1)['expected_read_blocks']

    if event['backend_path'] == 'decode':
        expected = {'source': 'paged_latent_and_rope', 'kv_length': start + 1, 'layout': 'BNSD_NBSD',
                    'sparse_mode': 0, 'block_size': kernel, 'has_mask': False, 'blocks': blocks(start + 1)}
        if count != 1 or event['decode'] != expected or event['history']:
            raise ValueError('MLA decode 有效历史范围、缓存块或读取来源不符')
        return
    expected = {'source': 'projected_current_input', 'query_length': count, 'kv_length': count,
                'layout': 'TND', 'sparse_mode': 3, 'has_mask': True, 'has_table': False}
    if event['backend_path'] != 'prefill' or event['current'] != expected:
        raise ValueError('MLA prefill 本块因果读范围不符')
    cursor = 0
    for chunk in event['history']:
        length = chunk['length']
        expected = {'start': cursor, 'length': length, 'blocks': blocks(cursor + length), 'fia_length': length,
                    'query_length': count, 'sparse_mode': 0, 'has_mask': False, 'layout': 'TND'}
        if length <= 0 or chunk != expected:
            raise ValueError('MLA 历史缓存加载和注意力实参不符')
        cursor += length
    if cursor != start:
        raise ValueError('MLA 已计算历史范围缺失')


def verify_mla(event, group, schedule):
    kernel = event['kernel_block_size']
    start, count = schedule['position'], schedule.get('count', 1)
    expected = write_slots(group, kernel, start, count)
    if kernel != 128 or any(event[k] != expected for k in ('expected_slots', 'metadata_slots', 'write_slots')):
        raise ValueError('MLA 调度、元数据与实际写槽不同')
    verify_reads(event, group, start, count)


def verify_components(event, files, count):
    widths = event['component_dims']
    projection = files['mla.projection.input']
    if projection['shape'] != [count, 1, 1, sum(widths)] or projection['dtype'] != 'torch.bfloat16':
        raise ValueError('MLA 融合输入的 dtype 或 shape 不符')
    for name, width, cache in zip(('latent', 'rope'), widths, event['cache_layouts'], strict=True):
        item = files['mla.'+name+'.after']
        if (cache['shape'][1:] != [128, 1, width] or item['shape'] != [count, 1, width]
                or cache['dtype'] != projection['dtype'] or item['dtype'] != projection['dtype']):
            raise ValueError('MLA 写后分量布局不符 ' + name)
        if event['backend_path'] == 'prefill':
            output = files['mla.'+name+'.output']
            if any(output[key] != item[key] for key in ('sha256', 'dtype', 'shape')):
                raise ValueError('MLA 逐位置写后值与融合输出不同 ' + name)


def physical_ranges(blocks, kernel, start, stop):
    ranges = []
    while start < stop:
        page, offset = divmod(start, kernel)
        count = min(kernel - offset, stop - start)
        slot = blocks[page] * kernel + offset
        ranges.append([slot, slot + count])
        start += count
    return ranges


def read_ranges(event, schedule):
    start = schedule['position']
    stop = start + schedule.get('count', 1)
    kernel = event['kernel_block_size']
    if event['backend_path'] == 'decode':
        slots = physical_ranges(event['decode']['blocks'], kernel, 0, stop)
        return [{'source': 'paged_latent_and_rope', 'logical_range': [0, stop], 'physical_slot_ranges': slots}]
    ranges = [{'source': 'projected_current_input', 'logical_range': [start, stop],
               'causal_end_exclusive': list(range(start + 1, stop + 1))}]
    for chunk in event['history']:
        begin = chunk['start']
        end = begin + chunk['length']
        ranges.append({'source': 'kv_cache_load', 'logical_range': [begin, end],
                       'physical_slot_ranges': physical_ranges(chunk['blocks'], kernel, begin, end)})
    return ranges
