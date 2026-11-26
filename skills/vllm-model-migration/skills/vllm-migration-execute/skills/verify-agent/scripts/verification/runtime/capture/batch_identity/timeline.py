"""用调度、worker 和采样原件构建单个成员的时间线。"""
from ..adapters.state import read
from ..tensors import file_ref


def bound(path, refs):
    ref = file_ref(path, path.parent)
    refs.append(ref)
    return read(path)


def actual_row(group, plan, mapped, intent, refs):
    fid, key = plan['forward_id'], mapped['internal_id']
    if plan['foreign_members']:
        raise ValueError('同批出现未登记请求，已跳过本批输入')
    frame = bound(group / ('worker-' + fid + '.json'), refs)
    if (frame['status'] != 'completed' or frame.get('error') or
            any(frame[k] != plan[k] for k in ('group_id', 'schedule_id', 'forward_id'))):
        raise ValueError(frame.get('error') or '模型没有正常完成本次前向')
    rows = frame['rows']
    if {r['internal_id'] for r in rows} != set(plan['requests']) or len(rows) != len(plan['requests']):
        raise ValueError('实际执行成员不符')
    row, = [r for r in rows if r['internal_id'] == key]
    if rows[row['batch_row']] != row or row['trace_id'] != intent['trace_id']:
        raise ValueError('前向的成员或 worker 行被错绑')
    return row


def describe(group, plan, mapped, intent, tokens, accepted, refs):
    key = mapped['internal_id']
    step = plan['requests'][key]
    fid = plan['forward_id']
    row = actual_row(group, plan, mapped, intent, refs)
    start, count = step['position'], step['count']
    stop = start + count
    sequence = mapped['prompt_ids'] + tokens
    phase = 'prefill' if start < len(mapped['prompt_ids']) else 'decode'
    if (step['phase'] != phase or row['positions'] != list(range(start, stop))
            or row['input_ids'] != sequence[start:stop] or step['trace_id'] != intent['trace_id']):
        raise ValueError('成员的实际输入、位置或调度阶段不同')
    samples = bound(group / ('samples-' + fid + '.json'), refs)
    if 'error' in samples:
        raise ValueError(samples['error'])
    sample = samples[key]
    index = None
    if stop >= len(mapped['prompt_ids']):
        index = step['output_index']
        record = accepted[index]
        if (record['forward_id'] != fid or record['internal_id'] != key or
                record['token_id'] != tokens[index] or sample['token_ids'] != [tokens[index]]):
            raise ValueError('采样行、引擎接受结果或 HTTP Token 不一致')
    elif sample['token_ids']:
        raise ValueError('无输出的 prefill 块含有采样输出')
    return {**row, 'phase': phase, 'position_range': [start, stop], 'sample_row': sample['sample_row'],
            'output_index': index, 'token_id': tokens[index] if index is not None else None}


def build(group, folder, mapped, intent, tokens):
    rows, refs, errors = [], [], []
    accepted = {item['output_index']: item for item in
                (bound(p, refs) for p in folder.glob('accepted-*.json'))}
    cursor = 0
    for path in sorted(group.glob('dispatch-*.json'), key=lambda p: int(p.stem.split('-')[1])):
        plan = read(path)
        key = mapped['internal_id']
        if key not in plan['requests']:
            continue
        refs.append(file_ref(path, group))
        row = {k: plan[k] for k in ('forward_id', 'schedule_id', 'ordinal', 'group_id')}
        try:
            if plan['group_id'] != group.name:
                raise ValueError('调度记录来自其他组')
            row.update(describe(group, plan, mapped, intent, tokens, accepted, refs))
            if row['position_range'][0] != cursor:
                raise ValueError('成员的前向位置不连续')
            row['status'] = 'completed'
        except (ValueError, OSError, KeyError, IndexError, TypeError) as error:
            row.update(status='incomplete', error=str(error))
            errors.append(row['forward_id'] + ' ' + str(error))
        step = plan['requests'][key]
        cursor = step['position'] + step['count']
        rows.append(row)
    indices = [r['output_index'] for r in rows if r.get('output_index') is not None]
    if not rows or indices != list(range(len(tokens))) or sorted(accepted) != indices:
        errors.append('已执行前向与逐个接受的输出无法一一对应')
    return rows, refs, errors
