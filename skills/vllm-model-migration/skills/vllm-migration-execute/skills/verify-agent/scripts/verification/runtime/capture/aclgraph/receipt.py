"""封存一次真实请求，分别检查调度、图重放、原生张量和状态快照。"""
import re

from ....evidence import read_bound, write_json
from ..adapters.state import read
from ..index import prediction_values
from ..parallel.receipt import scheduled_entries
from ..session_receipt import http_json, mapped_response
from ..tensors import file_ref
from .evidence import inspect_layer
from .native import call_info, tensor_header

EVIDENCE_ERRORS = (ValueError, KeyError, IndexError, OSError, TypeError)


def bind_worker(folder, ready):
    path = folder/'graph-worker.json'
    write_json(path, ready)
    return file_ref(path, folder)


def seal_worker(ref, inspect):
    ready = read_bound(ref)
    try:
        if inspect() != ready:
            raise ValueError('arm 与 seal 的 worker 身份不同')
    except (ValueError, OSError, RuntimeError) as error:
        return {**ready, 'worker_ref': ref, 'service_errors': [str(error)]}
    return {**ready, 'worker_ref': ref}


def native_index(root, worker, frame):
    step = frame['native_step']
    member = frame['native_rank']
    if type(step) != int or step < 0 or member not in ('rank0', f"pid{worker['pid']}"):
        raise ValueError('msProbe 原生步号或 worker 目录错误')
    directory = root / 'dump' / 'native' / f'step{step}' / member / 'dump_tensor_data'
    owners = {path: [] for path in worker['layers']}
    files, errors, size = [], [], 0
    for path in sorted(directory.glob('*.pt')):
        try:
            size += path.stat().st_size
            ref = {**file_ref(path, directory), **tensor_header(path), **call_info(path.name), 'bytes': path.stat().st_size}
        except (ValueError, OSError) as exc:
            errors.append(f'{path.name} {exc}')
            continue
        files.append(ref)
        for layer in owners:
            if path.name.startswith('Module.'+layer+'.'):
                owners[layer].append(ref)
                break
    return {'step': step, 'rank_dir': member, 'files': files, 'layers': owners,
            'bytes': size, 'errors': errors}


def verified_replays(root, frame, ready, ordinals, shapes):
    events = frame['replays']
    if not events or ready['graph']['requested_mode'] == 'FULL' and len(events) != 1:
        raise ValueError('目标前向缺少实际重放')
    checked_ordinals = dict(ordinals)
    for event in events:
        graph = event['graph_id']
        if not re.fullmatch('[a-f0-9]{32}', graph):
            raise ValueError('图实例 ID 无效')
        path = root / ('graph-' + graph + '.json')
        captured = read(path)
        if any(event[k] != captured[k] for k in ('descriptor','wrapper_id','graph_id','mode')):
            raise ValueError('重放与捕图记录身份不符')
        if event['ordinal'] <= checked_ordinals.get(graph, 0) or event['mode'] != ready['graph']['requested_mode']:
            raise ValueError('重放序号或模式不符')
        checked_ordinals[graph] = event['ordinal']
        event['capture'] = file_ref(path, root)
        event['capture_seconds'] = captured['capture_seconds']
    signature = [e['graph_id'] for e in events]
    bucket = (frame['phase'], events[0]['descriptor'])
    if shapes.setdefault(bucket, signature) != signature:
        raise ValueError('同一批次描述符的重放序列发生变化或缺少子图')
    ordinals.update(checked_ordinals)


def validated_frame(folder, item):
    fid = item['forward_id']
    frame = read(folder / (fid+'.json'))
    closed = read(folder / ('end-'+fid+'.json'))
    begin = read(folder / ('begin-'+fid+'.json'))
    if any(f['forward_id'] != fid or f['schedule_id'] != item['schedule_id'] for f in (frame, begin, closed)):
        raise ValueError('worker 前向或调度身份错误')
    if (closed['status'] != 'completed' or frame['input_ids'] != item['input_ids']
            or frame['positions'] != list(range(*item['position_range']))):
        raise ValueError('实际输入或位置与调度不符')
    return frame


def collect_frame(root, folder, ready, item, dispatch, mapped, tokens, history):
    errors = []
    row = {**item, 'errors': errors, 'layers': [], 'replays': [], 'graph_status': 'incomplete'}
    frame = validated_frame(folder, item)
    row.update({k: frame[k] for k in ('graph_mode','padded_tokens','forward_seconds','export_seconds',
                                        'device_snapshot_bytes','snapshot_bytes','peak_npu_allocated_bytes','address_checks','started_monotonic','finished_monotonic') if k in frame})
    errors.extend(frame['errors'])
    if frame.get('padded_tokens', 0) < len(item['input_ids']):
        errors.append('图有效输入行超过 padding 后的长度')
    row['replays'] = frame.get('replays', [])
    try:
        if frame.get('replay_errors'):
            raise ValueError('，'.join(frame['replay_errors']))
        verified_replays(root, frame, ready, history['ordinals'], history['signatures'])
        row['graph_status'] = 'complete'
    except (ValueError, KeyError, OSError) as exc:
        errors.append(str(exc))
    if item['output_index'] is not None:
        index = item['output_index']
        try:
            if frame.get('candidate') != tokens[index]:
                raise ValueError('worker 输出与引擎接受结果不符')
            if ready['graph']['native']:
                accepted = read(folder / f'accepted-{index}.json')
                row.update(prediction_values(folder, frame, accepted, ready, mapped, tokens, index))
        except EVIDENCE_ERRORS as exc:
            errors.append('采样 '+str(exc))
    if ready['graph']['native']:
        try:
            collect_native(root, ready, frame, history, row)
        except EVIDENCE_ERRORS as exc:
            errors.append('msProbe '+str(exc))
    if ready['graph']['observe_cache']:
        try:
            layer_data(folder, ready, item, dispatch, frame, history, row)
        except EVIDENCE_ERRORS as exc:
            errors.append('缓存 '+str(exc))
    return row


def collect_native(root, ready, frame, history, row):
    errors = row['errors']
    row['native'] = native_index(root, ready, frame)
    errors.extend(row['native']['errors'])
    if frame['native_step'] in history['steps']:
        errors.append('msProbe 原生步号重复')
    history['steps'].add(frame['native_step'])
    for layer, refs in row['native']['layers'].items():
        if not refs:
            errors.append(layer+' 缺少 msProbe 原生张量')


def layer_data(folder, ready, item, dispatch, frame, history, row):
    fid = item['forward_id']
    errors = row['errors']
    plan = read(folder / ('schedule-'+item['schedule_id']+'.json'))
    if any(plan[k] != v for k, v in dispatch.items()):
        raise ValueError('缓存分配与此次调度不符')
    for desc in ready['architecture']['layers']:
        observed = frame.get('observations', {}).get(str(desc['index']), {})
        data = inspect_layer(desc, observed, folder/('snapshots-'+fid), plan, frame, history['states'])
        row['layers'].append(data)
        errors.extend(f"{desc['index']}/{reason}" for reason in data['errors'])


def request_timeline(root, folder, ready, mapped, tokens, intent):
    history = {'steps': set(), 'ordinals': {}, 'signatures': {}, 'states': {}}
    timeline, errors = [], list(ready.get('service_errors', []))
    if mapped.get('mm_features'):
        errors.append('图采集仅支持纯文本请求')
    errors.extend(read(p)['error'] for p in folder.glob('error-*.json'))
    schedule_complete = True
    try:
        for entry, dispatch in scheduled_entries(folder, mapped, tokens, intent):
            try:
                row = collect_frame(root, folder, ready, entry, dispatch, mapped, tokens, history)
            except (KeyError, IndexError, ValueError, OSError) as exc:
                row = {**entry, 'graph_status': 'incomplete', 'errors': [str(exc)], 'layers': [], 'replays': []}
            path = folder / ('graph-forward-'+entry['forward_id']+'.json')
            write_json(path, row)
            measurements = {k: row[k] for k in ('forward_seconds', 'export_seconds', 'snapshot_bytes',
                'peak_npu_allocated_bytes', 'started_monotonic', 'finished_monotonic') if k in row}
            measurements['native_bytes'] = row.get('native', {}).get('bytes', 0)
            timeline.append({**entry, 'actual_mode': row.get('graph_mode'), 'metrics': measurements, 'row': file_ref(path, folder), 'graph_status': row['graph_status'],
                             'replays': row['replays'], 'errors': row['errors']})
            errors.extend(entry['forward_id']+' '+reason for reason in row['errors'])
    except EVIDENCE_ERRORS as exc:
        errors.append(str(exc))
        schedule_complete = False
    graph_complete = schedule_complete and bool(timeline) and all(row['graph_status'] == 'complete' for row in timeline)
    return timeline, errors, graph_complete


def seal(request, authority, handoff, root, folder, intent, ready):
    mapped, engine, response = mapped_response(folder, request)
    tokens = engine['token_ids']
    if response['choices'][0].get('token_ids') != tokens:
        raise ValueError('HTTP 返回与引擎接受序列不符')
    timeline, errors, graph_complete = request_timeline(root, folder, ready, mapped, tokens, intent)
    from .metrics import totals
    receipt = {'mode': 'aclgraph', 'metrics': totals(timeline), 'graph_status': 'complete' if graph_complete else 'incomplete', 'request_id': request['trace_id'], 'timeline': timeline,
               'token_ids': tokens, 'sampling': http_json(request['http']['request']), 'http': request['http'],
               'graph': ready['graph'], 'worker': ready.get('worker_ref') or file_ref(root/'worker.json', root),
               'engine_input': file_ref(folder/'request.json', folder), 'source': authority['source'],
               'preparation': handoff['worker_capture']['receipt'], 'inference_status': 'completed',
               'startup': [file_ref(p, root) for p in sorted(root.glob('startup-*.json'))],
               'cache_errors': errors, 'cache_status': 'incomplete' if errors else 'complete'}
    path = folder/'capture-receipt.json'
    write_json(path, receipt)
    return {'status': 'incomplete' if errors else 'completed', 'receipt': file_ref(path, folder),
            'trace_id': request['trace_id'], 'cache_status': receipt['cache_status'], 'cache_errors': errors}
