"""将同一物理前向中的原生调用、成员 logits 和缓存观察独立封存。"""

from ....evidence import write_json
from ..adapters.decode_cache.layer_capture import layer_errors
from ..adapters.state import read
from ..cache_evidence import WIDTH
from ..layer_evidence import plan_disagreements, verify_layer
from ..tensors import file_ref
from . import catalog, native

FAILURES = (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError)


def verify_logits(folder, frame, member, ready):
    item = member['logits']
    path = folder / (frame['forward_id'] + '-logits.bin')
    if (
        path.name != item['file']
        or item['size'] != ready['vocab_size']
        or path.stat().st_size != item['size'] * WIDTH[item['dtype']]
    ):
        raise ValueError('成员 logits 文件或词表长度不符')
    return {**item, **file_ref(path, folder), 'batch_row': member['batch_row']}


def continuity(layer, history, position):
    for call in layer['invocations']:
        for state in call['states']:
            if state['kind'] != 'gdn':
                continue
            for name in ('conv', 'ssm'):
                key = (state['address'], call['occurrence'], name)
                before = state['snapshots'][name + '.before']['sha256']
                if position and key in history and history[key] != before:
                    raise ValueError(name + ' 与该成员上一次写后值不同')
                history[key] = state['snapshots'][name + '.after']['sha256']


def verify_boundary(calls, row, frame, name):
    views = native.describe(calls, row, frame['signatures'], len(frame['positions']))
    boundary = [v for c in views if c['module'] == name for v in c['tensors'] if v['scope'] == 'member_tokens']
    if not any(v['argument'][0] == 'output' for v in boundary) or not any(
        v['argument'][0].startswith('input_') and len(v['shape']) == 2 for v in boundary
    ):
        raise ValueError('msProbe 层输入输出缺少成员轴')


def layer_record(descriptor, cache, sources, row, frame, folder, schedule, history):
    name = descriptor['path']
    result = dict(index=descriptor['index'], layer=name, errors=[])
    calls = []
    try:
        result['native'] = sources['layers'][name]
        calls = catalog.calls_for(result['native'], row, name)
        result['errors'].extend(sources['errors'].get(name, ()))
        verify_boundary(calls, row, frame, name)
    except FAILURES as error:
        result['errors'].append('msProbe ' + str(error))
    if cache is not None:
        try:
            (layer,) = [entry for entry in cache if entry['index'] == descriptor['index'] and entry['path'] == name]
            result['cache'] = layer
            problems = layer_errors(layer)
            layer['status'] = 'incomplete' if problems else 'complete'
            result['errors'].extend(problems)
            directory = folder / ('cache-' + row['forward_id']) / ('layer-' + str(layer['index']))
            verify_layer(layer, descriptor, directory, {'calls': calls, 'decode_cache': {'schedule': schedule}})
            if not problems:
                result['errors'].extend(plan_disagreements(layer))
                continuity(layer, history, schedule['position'])
        except FAILURES as error:
            result['errors'].append('mindie ' + str(error))
    return result


def snapshot_bytes(layer):
    total = 0
    for call in layer.get('cache', {}).get('invocations', ()):
        snapshots = [call.get('boundary', {}), *(s['snapshots'] for s in call['states'])]
        total += sum(item['bytes'] for files in snapshots for item in files.values())
    return total


def record(group, folder, ready, row, enabled, history):
    fid = row['forward_id']
    frame_path = group / ('numeric-' + fid + '.json')
    frame = read(frame_path)
    if frame['forward_id'] != fid or frame['group_id'] != group.name or frame['profile'] != 'batch_joint':
        raise ValueError('数值前向与成员身份不同')
    actual = read(group / ('worker-' + fid + '.json'))['rows']
    if frame['positions'] != [position for item in actual for position in item['positions']]:
        raise ValueError('物理张量窗口的位置与冻结的 worker 批次不同')
    member = frame['rows'][row['batch_row']]
    if member['trace_id'] != row['trace_id']:
        raise ValueError('数值窗口的成员行顺序不同')
    if any(
        member[k] != row[k]
        for k in ('internal_id', 'batch_row', 'input_ids', 'positions', 'token_range', 'logits_index')
    ):
        raise ValueError('数值窗口与实际执行的成员或区间不同')
    trace = row['trace_id']
    result = dict(
        request_id=trace,
        forward_id=fid,
        group_id=group.name,
        errors=[*frame['errors'], *frame.get('member_errors', {}).get(trace, ())],
        layers={},
        signatures=frame.get('signatures', {}),
        total_tokens=len(frame['positions']),
        worker_numeric=file_ref(frame_path, group),
    )
    sources = {}
    try:
        sources = catalog.load(group, ready, frame)
        result['errors'].extend(sources['errors'].get('', ()))
        result.update(
            step=sources['step'], native_bytes_shared=sources['bytes'], native_metadata=sources['metadata']
        )
    except FAILURES as error:
        result['errors'].append('msProbe ' + str(error))
    try:
        result['logits'] = verify_logits(folder, frame, member, ready)
    except FAILURES as error:
        result['errors'].append('logits ' + str(error))
    plan = read(group / f'dispatch-{row["ordinal"]}.json')
    schedule = plan['requests'][member['internal_id']]
    cache = frame.get('cache', {}).get(trace, []) if enabled else None
    result['msprobe_window_seconds_shared'] = frame.get('probe_window_seconds')
    result['cache_observer_seconds_shared'] = frame.get('observer_seconds')
    result['cache_bytes'] = 0
    for desc in ready['architecture']['layers']:
        layer = layer_record(desc, cache, sources, row, frame, folder, schedule, history)
        result['cache_bytes'] += snapshot_bytes(layer)
        path = folder / f'layer-{fid}-{desc["index"]}.json'
        write_json(path, {**layer, 'request_id': trace, 'forward_id': fid, 'group_id': group.name})
        result['layers'][str(desc['index'])] = file_ref(path, folder)
        result['errors'].extend(f'layer {desc["index"]} {e}' for e in layer['errors'])
    return result


def attach(group, folder, rows, config, errors):
    ready = read(group.parent / 'worker.json')
    history = {}
    for row in rows:
        fid = row['forward_id']
        try:
            result = record(group, folder, ready, row, 'cache_capture' in config, history)
            path = folder / ('member-numeric-' + fid + '.json')
            write_json(path, result)
            row['numeric'] = file_ref(path, folder)
            row['capture_totals'] = {
                k: result.get(k)
                for k in (
                    'native_bytes_shared',
                    'cache_bytes',
                    'msprobe_window_seconds_shared',
                    'cache_observer_seconds_shared',
                )
            }
            row['numeric_status'] = 'incomplete' if result['errors'] else 'completed'
            errors.extend(f'{fid} {folder.name} {e}' for e in result['errors'])
        except FAILURES as error:
            row['numeric_status'] = 'incomplete'
            errors.append(f'{fid} {folder.name} {error}')
