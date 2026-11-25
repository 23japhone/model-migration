"""逐 rank 核对实收输入、原生 msProbe 数据、采样和缓存快照。"""
from ....evidence import write_json
from ..adapters.state import read
from ..cache_evidence import from_frame
from ..index import native_forward, prediction_values
from ..session_receipt import require_coverage
from ..tensors import file_ref
from ..timeline import disk_bytes, gdn_continuity

DATA_ERRORS = (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError)


def observed(folder, identity, member, ids):
    fid, rank = identity['forward_id'], member['rank']
    begin = read(folder / f'begin-{fid}.json')
    frame = read(folder / f'end-{fid}.json')
    expected = list(range(identity['position'], identity['position'] + identity['count']))
    for witness in (begin, frame):
        if (witness['dispatch'] != identity or witness['forward_id'] != fid or witness['rank'] != rank
                or witness['positions'] != expected or witness['input_ids'] != ids
                or witness['internal_id'] != identity['internal_id'] or witness['output_index'] != identity['output_index']):
            raise ValueError('广播的身份、worker 实收 Token 或位置不同')
    if 'vision' in frame:
        axes = frame.get('position_axes', [])
        if len(axes) != 3 or any(len(axis) != len(expected) for axis in axes) or axes != begin.get('position_axes'):
            raise ValueError('M-RoPE 位置轴长度或前向身份不符')
    if frame['status'] != 'completed':
        raise ValueError('worker 执行未完成')
    return frame


def sampling(folder, frame, accepted, member, mapped, tokens):
    index = accepted['output_index']
    pred = read(folder / f'prediction-{index}.json')
    if (pred != read(folder / (frame['forward_id'] + '.json')) or pred['dispatch'] != frame['dispatch']
            or pred['rank'] != member['rank'] or pred['candidate'] != accepted['token_id']
            or pred['positions'] != frame['positions'] or pred['input_ids'] != frame['input_ids']):
        raise ValueError('输出 rank 实际候选与引擎接受结果不符')
    return prediction_values(folder, pred, accepted, member, mapped, tokens, index)


def rank_frame(root, folder, member, entry, dispatch, intent, mapped, tokens, previous):
    rank = member['rank']
    directory = folder / f'rank{rank}'
    frame = observed(directory, dispatch, member, entry['input_ids'])
    fid = entry['forward_id']
    row = {k: entry[k] for k in ('forward_id', 'output_index', 'token_id', 'input_ids', 'prefix_ids', 'schedule_id')}
    row.update(client_request_id=intent['request_id'], internal_id=mapped['internal_id'], positions=frame['positions'],
               rank=rank, dispatch=dispatch, step=frame['step'], layers=member['layers'], architecture=member['architecture'],
               scheduled_output_index=dispatch['output_index'], state_shards=member['state_shards'])
    errors = []
    native_bytes = disk_bytes(root / 'dump' / 'native' / f"step{frame['step']}" / f'rank{rank}')
    try:
        native = native_data(root, member, frame, row)
        row.update(native)
    except DATA_ERRORS as error:
        errors.append('msProbe: ' + str(error))
        row.update(native_metadata=[], calls=[], coverage={'status': 'incomplete', 'error': str(error)})
    if 'vision' in frame:
        from ..generation.vision import witness
        row['position_axes'] = frame['position_axes']
        try:
            row['vision'] = witness(directory, frame, member, 'cache_capture' in intent, row['calls'], errors)
        except DATA_ERRORS as error:
            errors.append(f'图像观测: {error}')
    if rank == 0 and row['output_index'] is not None:
        try:
            row.update(sampling(directory, frame, entry, member, mapped, tokens))
        except DATA_ERRORS as error:
            errors.append('采样行异常 ' + str(error))
    row.update(from_frame(directory, frame, intent, member, row['calls']))
    cache = row.get('decode_cache', {})
    errors.extend(cache.get('errors', []))
    errors.extend(gdn_continuity(cache, previous, dispatch['position']))
    path = directory / f'forward-{fid}.json'
    write_json(path, row)
    missing = [layer['path'] for layer in cache.get('layers', []) if layer['status'] != 'complete']
    return {'rank': rank, 'missing_layers': missing,
            **({'position_axes': frame['position_axes']} if 'vision' in frame else {}),
            'msprobe_status': row['coverage']['status'], 'cache_observation_status': cache.get('status', 'not_requested'), 'step': frame['step'], 'row': file_ref(path, directory), 'errors': errors,
            'status': 'incomplete' if errors else 'complete', 'layer_count': len(member['layers']),
            'msprobe_bytes': native_bytes, 'msprobe_window_seconds': frame['probe_window_seconds'],
            'snapshot_bytes': disk_bytes(directory / f'cache-{fid}') + disk_bytes(directory / f'vision-{fid}'), 'observer_seconds': cache.get('observer_seconds', 0) + frame.get('vision', {}).get('observer_seconds', 0),
            'begin': file_ref(directory / f'begin-{fid}.json', directory), 'end': file_ref(directory / f'end-{fid}.json', directory)}


def native_data(root, member, frame, row):
    offset = frame['logits_indices'][0] if row['output_index'] is not None else 0
    dump, calls, coverage = native_forward(root, member, frame, offset)
    require_coverage(coverage)
    if row['output_index'] is None:
        for call in calls:
            for side in call['tensors'].values():
                for tensor in side:
                    tensor['slice'] = 'whole_tensor'
    return dict(calls=calls, coverage=coverage, native_metadata=[file_ref(dump / n, dump) for n in ('dump.json', 'construct.json')])


def missing_member(root, folder, worker, entry, dispatch, intent, error):
    rank, fid = worker['rank'], entry['forward_id']
    directory = folder / f'rank{rank}'
    result = {'rank': rank, 'status': 'incomplete', 'errors': [str(error)], 'missing_layers': list(worker['layers']),
              'msprobe_status': 'incomplete', 'cache_observation_status': 'incomplete' if 'cache_capture' in intent else 'not_requested',
              'snapshot_bytes': disk_bytes(directory / f'cache-{fid}')}
    for name in ('end', 'begin'):
        path = directory / f'{name}-{fid}.json'
        if not path.is_file():
            continue
        try:
            witness = read(path)
            if witness['dispatch'] != dispatch or witness['rank'] != rank or witness['forward_id'] != fid:
                continue
            result[name] = file_ref(path, directory)
            dump = root / 'dump' / 'native' / f"step{witness['step']}" / f'rank{rank}'
            if root / witness['dump'] == dump:
                result.update(msprobe_bytes=disk_bytes(dump), step=witness['step'])
        except DATA_ERRORS as diagnostic:
            result['errors'].append(f'{name}: {diagnostic}')
    return result
