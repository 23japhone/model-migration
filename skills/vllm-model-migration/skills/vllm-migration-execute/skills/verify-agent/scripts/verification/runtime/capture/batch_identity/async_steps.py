"""按不可变来源核对提交、异步完成、引擎接受及归档。"""
from itertools import chain

from ..adapters import state
from ..adapters.async_batch.engine import identity


def waiting(group, trace):
    outstanding = []
    for path in chain(group.glob('inflight-*.json'), group.glob('exporting-*.json')):
        try:
            if trace in state.read(path)['members']:
                outstanding.append(path.stem.split('-', 1)[1])
        except FileNotFoundError:
            pass  # 引擎在扫描时完成了该提交。
    return sorted(set(outstanding))


def events(group, plan, key, sample, accepted, read):
    fid = plan['forward_id']
    origin = list(identity(plan))
    submit, ready, retired = [read(group / (name + '-' + fid + '.json')) for name in ('submit', 'ready', 'retired')]
    if any(event['origin'] != origin for event in (submit, ready, retired)) or retired['failed']:
        raise ValueError('异步事件身份不一致或引擎处理失败')
    if retired['retired_ns'] < ready['ready_ns']:
        raise ValueError('引擎处理完成时间早于 worker 输出就绪')
    row = sample['sample_row']
    if ready['req_ids'][row] != key or ready['sample_rows'][key] != row or ready['tokens'][row] != sample['token_ids']:
        raise ValueError('异步 worker 输出与引擎接收的成员行不同')
    overlaps, unconfirmed = overlap_ids(group, fid, submit, read)
    index = accepted_index(retired, key, fid, accepted)
    return {'async_events': {'submitted_ns': submit['submitted_ns'], 'ready_ns': ready['ready_ns'],
                             'retired_ns': retired['retired_ns'], 'overlaps': overlaps,
                             'overlap_unconfirmed': unconfirmed,
                             'queue_depth': submit['queue_depth'], 'disposition': retired['dispositions'][key]},
            'output_index': index}


def overlap_ids(group, fid, submit, read):
    overlaps, unconfirmed = [], []
    for prior in submit['previous_pending']:
        previous_submit = read(group / ('submit-' + prior + '.json'))
        origin = previous_submit['origin']
        if (origin[0] != group.name or origin[2] != prior or prior == fid
                or previous_submit['submitted_ns'] > submit['submitted_ns']):
            raise ValueError('在途前向身份不符')
        ready_path = group / ('ready-' + prior + '.json')
        if not ready_path.exists():
            unconfirmed.append(prior)
            continue
        prev = read(ready_path)
        if prev['origin'] != origin:
            raise ValueError('在途输出与其提交身份不符')
        if submit['submitted_ns'] < prev['ready_ns']:
            overlaps.append(prior)
    return overlaps, unconfirmed


def accepted_index(retired, key, fid, accepted):
    disposition = retired['dispositions'][key]
    index = retired['indices'].get(key)
    if disposition == 'accepted':
        if type(index) is not int or accepted[index]['forward_id'] != fid:
            raise ValueError('接受序号与异步前向不一致')
    elif disposition not in ('discarded', 'finished_or_aborted', 'prefill_no_output') or index is not None:
        raise ValueError('缺少本次前向的引擎接受或丢弃依据')
    return index
