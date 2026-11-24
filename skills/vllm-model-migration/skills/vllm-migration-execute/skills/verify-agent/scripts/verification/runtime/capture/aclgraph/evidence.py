"""按本次图前向的文件、实际有效位置和调度分配核验本地层数据。"""
import math
from pathlib import Path
import struct

from ..adapters.decode_cache.plan import attention_plan, state_row
from ..adapters.decode_cache.prefill_attention import write_slots
from ..tensors import file_ref

INTEGER = {'torch.bool': '?', 'torch.int32': 'i', 'torch.int64': 'q'}
WIDTH = {'torch.bfloat16': 2, 'torch.float16': 2, 'torch.float32': 4, 'torch.float64': 8,
         **{k: struct.calcsize('<'+v) for k, v in INTEGER.items()}}


def integers(ref):
    return [v[0] for v in struct.iter_unpack('<'+INTEGER[ref['dtype']], Path(ref['path']).read_bytes())]


def checked_file(folder, name, record):
    if Path(name).name != name or record['file'] != name+'.bin':
        raise ValueError('快照文件名不符')
    path = folder / record['file']
    ref = file_ref(path, folder)
    if ref['sha256'] != record['sha256'] or path.stat().st_size != math.prod(record['shape'])*WIDTH[record['dtype']]:
        raise ValueError('原值长度或摘要失配')
    return {**record, **ref, 'size': math.prod(record['shape'])}


def check_attention(layer, plan, group, frame):
    files = layer['files']
    slots = integers(files['write.slots'])
    count = plan['count']
    read = frame['backend_reads'][str(layer['index'])]
    kernel = read['block_size']
    expected = write_slots(group, kernel, plan['position'], count)
    layer['plan'] = {'expected_slots': expected, 'write_slots': slots[:count], 'padding_slots': slots[count:],
                     'read': read, **attention_plan(group['blocks'], group['page_size'], kernel, plan['position']+count-1)}
    if slots[:count] != expected or any(slot >= 0 for slot in slots[count:]):
        raise ValueError('有效写槽或 padding 槽不符')
    if (read['kv_length'] != plan['position']+count or read['query_length'] != count
            or read['source'] == 'paged_kv' and read['blocks'] != layer['plan']['expected_read_blocks']
            or read['source'] == 'input_kv' and plan['position'] != 0):
        raise ValueError('注意力实际读取范围与调度不符')
    if read['backend'] == 'FIA' and read['sparse_mode'] != 3:
        raise ValueError('FIA 因果约束未核对')
    layer['plan']['causal_end_exclusive'] = list(range(plan['position']+1, read['kv_length']+1))
    for name in ('key', 'value'):
        if any(files[name+'.after'][k] != files[name+'.input'][k] for k in ('sha256', 'shape', 'dtype')):
            raise ValueError(name+' 写后值与输入不同')


def check_gdn(layer, plan, group, frame, previous):
    files = layer['files']
    row, = integers(files['state.row'])
    expected = state_row(group['blocks'], group['page_size'], plan['position']+plan['count']-1, frame['mamba_mode'])
    if row != expected:
        raise ValueError('GDN 状态行与调度不符')
    layer['plan'] = {'state_row': row, 'expected_row': expected, 'granularity': 'forward_chunk'}
    call = frame['gdn_calls'][str(layer['index'])]
    if call['conv_rows'] != [row] or call['query_start'] != [0, plan['count']]:
        raise ValueError('卷积实收的状态行或位置区间不符')
    layer['plan']['conv_call'] = call
    if call['run_mode'] == 0:
        check_chunk_initial(layer, plan, call)
    elif call['run_mode'] != 1 or integers(files['ssm.row']) != [row]:
        raise ValueError('递归算子实际状态行或卷积模式不符')
    else:
        layer['plan']['initial_source'] = 'cache'
    for name in ('conv', 'ssm'):
        key = layer['index'], name
        before, after = (files[name+'.'+side]['sha256'] for side in ('before', 'after'))
        if plan['position'] and previous.get(key) != before:
            raise ValueError(name+' 与上一块的终态不符')
        previous[key] = after


def check_chunk_initial(layer, plan, call):
    files = layer['files']
    initial, = integers(files['state.initial'])
    layer['plan'].update(has_initial_state=bool(initial), initial_source='cache' if initial else 'zero')
    if bool(plan['position']) != bool(initial) or list(map(bool, call['initial_mode'])) != [bool(initial)]:
        raise ValueError('GDN 初态来源不符')
    if initial:
        valid = files['ssm.initial']['sha256'] == files['ssm.before']['sha256']
    else:
        valid = not any(Path(files['ssm.initial']['path']).read_bytes())
    if not valid or files['ssm.final']['sha256'] != files['ssm.after']['sha256']:
        raise ValueError('GDN 算子初态或终态与物理写回不符')


def check_source(source, frame):
    if source.get('kind') == 'outside_graph' and source.get('forward_id') == frame['forward_id']:
        return
    if source.get('kind') == 'graph' and any(
        (source.get('graph_id'), source.get('ordinal')) == (e['graph_id'], e['ordinal']) for e in frame['replays']
    ):
        return
    raise ValueError('快照来源与实际图重放或图外调用不符')


def inspect_layer(descriptor, observed, folder, plan, frame, previous):
    index = descriptor['index']
    item = {'index': index, 'path': descriptor['path'], 'kind': descriptor['kind'],
            'files': {}, 'sources': observed.get('sources', {}), 'errors': []}
    for name, value in observed.get('files', {}).items():
        try:
            check_source(item['sources'].get(name, {}), frame)
            item['files'][name] = checked_file(folder / str(index), name, value)
        except (ValueError, KeyError, OSError) as exc:
            item['errors'].append(name+' '+str(exc))
    try:
        for edge in ('input', 'output'):
            if edge not in item['files']:
                item['errors'].append('缺少层边界 '+edge)
        target, = descriptor['states']
        group, = [g for g in plan['groups'] if target['address'] in g['layers']]
        if item['kind'] == 'attention':
            check_attention(item, plan, group, frame)
        elif item['kind'] == 'gdn':
            check_gdn(item, plan, group, frame, previous)
        else:
            raise ValueError('未知状态后端 '+item['kind'])
    except (KeyError, IndexError, ValueError, OSError) as exc:
        item['errors'].append(str(exc))
    return item
