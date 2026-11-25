"""按逻辑前向读取各 rank 本地原值，隔离单个成员的证据缺失。"""
from pathlib import Path
from ....evidence import read_bound
from .. import cache_evidence
from ..index import selected, verify_tensors
from ..generation.delivery import load_response
from ..generation.query import event_view, image_detail, image_for
from ..timeline import select_entry
from .frames import DATA_ERRORS
from .topology import device_identity, group_size


def rank_view(result, entry, member, request, folder):
    rank = member['rank']
    directory = folder / f'rank{rank}'
    ref = member.get('row')
    if ref is None:
        return member
    fid = entry['forward_id']
    if Path(ref['path']) != directory / f'forward-{fid}.json':
        raise ValueError('rank 行引用跨目录')
    row = bound_member(result, entry, member, rank, fid)
    dump = folder.parent / 'dump' / 'native' / f"step{row['step']}" / f'rank{rank}'
    request, extra = select_image_row(row, request, directory)
    errors = inspect_cache(row, member, directory, fid, result)
    if errors and 'cache_snapshot' in request:
        try:
            detail = selected(row, request)
        except ValueError as error:
            return {**member, 'status': 'incomplete', 'errors': [*errors, str(error)]}
    else:
        detail = selected(row, request)
    detail.update(extra)
    errors.extend(validate_native_selection(row, detail, fid, dump))
    status = 'incomplete' if errors else member['status']
    if 'rank' not in request:
        return {**member, 'status': status, 'errors': errors, 'native_metadata': detail['native_metadata'],
                'state_shards': row.get('state_shards', {}), 'layers': row['layers'],
                **({'vision': row['vision']} if 'vision' in row else {})}
    return {**detail, 'device': member.get('device') or device_identity(read_bound(result['workers'][rank])),
            'status': status, 'errors': errors}


def bound_member(result, entry, member, rank, fid):
    ref = member['row']
    row = read_bound(ref)
    worker = bound_worker(result, member, Path(ref['path']).parent.parent)
    if (row['rank'], worker['rank'], row['forward_id'], row['client_request_id'], row['output_index'],
            row['positions'], row['input_ids'], row['architecture']) != (
            rank, rank, fid, result['request_id'], entry['output_index'], list(range(*entry['position_range'])),
            entry['input_ids'], worker['architecture']):
        raise ValueError('所选 rank 的执行身份、结构或输入不符')
    folder = Path(ref['path']).parent
    for name in ('begin', 'end'):
        if Path(member[name]['path']) != folder / f'{name}-{fid}.json':
            raise ValueError('前向记录引用跨 rank')
    begin, end = read_bound(member['begin']), read_bound(member['end'])
    if (begin['rank'], end['rank'], end['step']) != (rank, rank, row['step']):
        raise ValueError('执行记录与本卡身份或 step 不符')
    if begin['dispatch'] != end['dispatch'] or end['dispatch'] != read_bound(entry['dispatch']):
        raise ValueError('worker 与调度前向不符')
    if row.get('dispatch', end['dispatch']) != end['dispatch']:
        raise ValueError('worker 与调度前向不符')
    return row


def bound_worker(result, member, folder):
    rank = member['rank']
    ref = result['workers'][rank]
    allowed = (folder / f'member-rank{rank}.json', folder.parent / f'worker-rank{rank}.json')
    if Path(ref['path']) not in allowed:
        raise ValueError('worker 身份引用跨请求或 rank')
    worker = read_bound(ref)
    if 'device' in member and member['device'] != device_identity(worker):
        raise ValueError('rank 设备身份与 worker 凭证不同')
    return worker


def inspect_cache(row, member, directory, fid, result):
    rank = member['rank']
    errors = list(member['errors'])
    if 'decode_cache' in row:
        from .snapshots import isolate_files
        errors.extend(isolate_files(directory / f'cache-{fid}', row['decode_cache']))
        cache_evidence.verify(directory, row['decode_cache'], row)
    for name in ('raw', 'scores', 'probabilities'):
        if name in row and (rank != result['output_rank'] or Path(row[name]['path']).parent != directory):
            raise ValueError('预测向量的 rank 不符')
    return errors


def verify_metadata(row, dump):
    for metadata in row['native_metadata']:
        if Path(metadata['path']).parent != dump:
            raise ValueError('msProbe 原生数据引用跨 rank')
        read_bound(metadata)


def verify_selected_calls(detail, fid, dump):
    for call in detail['calls']:
        for side in call['tensors'].values():
            if any('path' in t and Path(t['path']).parent != dump / 'dump_tensor_data' for t in side):
                raise ValueError('张量引用跨 rank')
        verify_tensors(fid, call)


def validate_native_selection(row, detail, fid, dump):
    try:
        verify_metadata(row, dump)
        verify_selected_calls(detail, fid, dump)
    except DATA_ERRORS as error:
        if not ({'cache_snapshot', 'image_snapshot'} & detail.keys()):
            raise
        message = f'msProbe {fid} rank{row["rank"]}: {error}'
        detail.update(calls=[], native_metadata=[], coverage={'status': 'incomplete', 'error': message})
        return [message]
    return []


def verify_request(result):
    for key in ('engine', 'executor', 'derived_config', 'engine_input', 'frontend'):
        read_bound(result[key])
    if result.get('delivery', {}).get('frontend_engine_result_observed', True):
        read_bound(result['engine_output'])
    preparation = read_bound(result['preparation'])
    size = group_size(read_bound(preparation['parallel']))
    if size != result['tensor_parallel_size']:
        raise ValueError('回执的 TP 大小与部署证据不符')
    if 'cache_capture' in result:
        read_bound(preparation['decode_cache'])
    load_response(result['http'], partial=result.get('cache_status') == 'incomplete')
    return size


def local_members(entry, ranks):
    by_rank = {}
    for member in entry['ranks']:
        rank = member['rank']
        by_rank[rank] = None if rank in by_rank else member
    return [by_rank.get(rank) or {'rank': rank, 'status': 'incomplete',
            'errors': [f"{entry['forward_id']} rank{rank}: 缺少唯一成员记录"]} for rank in ranks]


def timeline(result, ranks):
    items = []
    for item in result['timeline']:
        read_bound(item['dispatch'])
        if 'accepted' in item:
            read_bound(item['accepted'])
        items.append({**item, 'ranks': local_members(item, ranks)})
    return {**result, 'timeline': items}


def lookup(result, request):
    if result['request_id'] != request['request_id']:
        raise ValueError('请求 ID 与多卡回执不匹配')
    folder = Path(request['source']['path']).parent
    size = verify_request(result)
    if request.get('rank', 0) not in range(size):
        raise ValueError(f'所选 rank 超出回执范围 0..{size - 1}')
    ranks = (request['rank'],) if 'rank' in request else range(size)
    if 'image_index' in request and request.keys() <= {'image_index', 'request_id', 'source', 'rank'}:
        image = image_for(result, request)
        return {'request_id': result['request_id'], 'image': {**image, 'ranks': [image['ranks'][r] for r in ranks]}}
    if 'event_index' in request:
        return event_view(result, request['event_index'])
    if 'view' in request:
        return timeline(result, ranks)
    entry, linked = resolve_entry(result, request, ranks)
    members = []
    for member in local_members(entry, ranks):
        rank = member['rank']
        try:
            members.append(rank_view(result, entry, member, request, folder))
        except DATA_ERRORS as error:
            members.append({'rank': rank, 'status': 'incomplete', 'errors': [f"{entry['forward_id']} rank{rank}: {error}"]})
    status = 'incomplete' if any(m['status'] != 'complete' for m in members) else 'complete'
    overall = 'incomplete' if 'incomplete' in (status, result.get('cache_status')) else 'complete'
    if 'rank' in request:
        return {**entry, **linked, **members[0], 'request_status': overall, 'request_id': result['request_id'], 'ranks': list(ranks)}
    return {**entry, **linked, 'request_id': result['request_id'], 'request_status': overall, 'status': status, 'ranks': members}


def resolve_entry(result, request, ranks):
    if set(request) & {'forward_id', 'prompt_position'}:
        entry = select_entry(result['timeline'], request)
    else:
        entry = output_entry(result['timeline'], request)
    if 'accepted' in entry:
        read_bound(entry['accepted'])
    linked = {}
    if set(request) & {'image_index', 'image_snapshot'} or request.get('stack') == 'vision':
        image = image_for(result, request)
        linked = {'image': image, 'text_forward_id': entry['forward_id']}
        fusion = request.get('image_snapshot', '').startswith('fusion.')
        if not fusion and (request.get('stack') == 'vision' or 'image_snapshot' in request):
            ids = {fid for rank in ranks for fid in image['ranks'][rank]['encoder_forward_ids']}
            if len(ids) != 1:
                raise ValueError('缺少唯一视觉编码前向')
            entry, = [e for e in result['timeline'] if e['forward_id'] in ids]
    return entry, linked


def output_entry(entries, request):
    if 'output_index' not in request and set(request) & {'image_index', 'image_snapshot', 'stack'}:
        return entries[0]
    index = request.get('output_index', 1)
    entry, = [e for e in entries if e['output_index'] == index]
    return entry


def select_image_row(row, request, directory):
    extra = {}
    if request.get('stack') == 'vision' or 'image_snapshot' in request:
        layers = row['architecture']['other_stacks']['visual.blocks']
        if 'layer_index' in request and request.get('stack') == 'vision':
            request = dict(request)
            index = request.pop('layer_index')
            request['layer'] = layers[index]['path']
        if 'image_snapshot' in request and request.get('layer', '').startswith('visual.'):
            name = request['image_snapshot']
            if not name.startswith(request['layer'] + '.'):
                raise ValueError('视觉快照与所选层不匹配')
        extra = image_detail(row, request, directory)
    return request, extra
