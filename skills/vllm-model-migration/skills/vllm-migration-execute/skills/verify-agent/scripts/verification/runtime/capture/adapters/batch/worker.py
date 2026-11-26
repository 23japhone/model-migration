"""在 _model_forward 边界冻结有效 Token 行；独立于 msProbe 的数值采集。"""
from contextlib import contextmanager
from .. import state


def text_positions(positions, count):
    axes = positions[..., :count].tolist()
    if positions.ndim == 1:
        return axes
    if any(axis != axes[0] for axis in axes):
        raise ValueError('批次文本位置轴不一致')
    return axes[0]


def input_rows(planned, runner, ids, positions):
    req_ids = list(runner.input_batch.req_ids)
    if set(req_ids) != set(planned) or len(req_ids) != len(planned):
        raise ValueError('worker 实际请求与调度成员不一致')
    offsets = runner.query_start_loc.np[:len(req_ids) + 1].tolist()
    if len(offsets) != len(req_ids) + 1 or offsets[0] != 0:
        raise ValueError('worker 有效 Token 边界缺失')
    count = offsets[-1]
    source = runner.input_ids.gpu if ids is None else ids
    tokens = source[:count].tolist()
    absolute = text_positions(positions, count)
    logits = runner.logits_indices[:len(req_ids)].tolist()
    if len(tokens) != count or logits != [end - 1 for end in offsets[1:]]:
        raise ValueError('有效 Token 或 greedy logits 索引不一致')
    rows = []
    for row, internal in enumerate(req_ids):
        start, end = offsets[row:row + 2]
        item = planned[internal]
        expected = range(item['position'], item['position'] + item['count'])
        if end - start != item['count'] or absolute[start:end] != list(expected):
            raise ValueError('实收 Token 数或绝对位置与调度不符')
        rows.append({'internal_id': internal, 'trace_id': item['trace_id'], 'batch_row': row,
                     'token_range': [start, end], 'input_ids': tokens[start:end],
                     'positions': absolute[start:end], 'logits_index': logits[row]})
    return rows


@contextmanager
def forward(plan, runner, ids, positions):
    group = state.root() / plan['group_id']
    path = group / ('worker-' + plan['forward_id'] + '.json')
    record = {key: plan[key] for key in ('group_id', 'schedule_id', 'forward_id')}
    try:
        if path.exists():
            raise ValueError('一次调度进入多次模型前向')
        record['rows'] = input_rows(plan['requests'], runner, ids, positions)
        state.write(path, {**record, 'status': 'entered'})
    except Exception as error:
        record['error'] = str(error)
    try:
        yield record.get('rows') if 'error' not in record else None
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        try:
            state.write(path, {'status': 'completed', **record})
        except OSError as error:
            state.fail(group, error)


def capable(runner, size):
    scheduler = getattr(runner.vllm_config, 'scheduler_config', None)
    cache = getattr(runner.vllm_config, 'cache_config', None)
    dtype = str(getattr(runner.model_config, 'dtype', ''))
    return (size == 1 and getattr(scheduler, 'max_num_seqs', 0) >= 2
            and getattr(cache, 'enable_prefix_caching', True) is False and dtype in ('torch.bfloat16', 'bfloat16'))
