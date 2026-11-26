"""按请求与输出序号联结 prompt、前向、采样和实际接收证据。"""
import hashlib
import math
from pathlib import Path
import struct

from ...evidence import read_bound, write_json
from .adapters.state import read
from . import cache_evidence
from .tensors import file_ref, inventory, owner


def correlate(prompt, outputs, index, forward, candidate, accepted):
    prefix = [*prompt, *outputs[:index]]
    positions, ids = forward['positions'], forward['input_ids']
    if not positions or len(positions) != len(ids) or not 0 <= index < len(outputs):
        raise ValueError(f'输出 {index} 缺少有效输入行')
    row, = forward['logits_indices']
    if (positions != list(range(positions[0], positions[0] + len(ids))) or positions[0] < 0
            or prefix[positions[0]:positions[-1]+1] != ids or not 0 <= row < len(ids)
            or positions[row] != len(prefix)-1 or outputs[index] != accepted or accepted != candidate):
        raise ValueError(f'输出 {index} 的前文、logits 行、候选或接受值不符')
    return prefix


def vector(item, *, allow_nan=False):
    path = Path(item['path'])
    if path.resolve() != path or not path.is_file():
        raise ValueError(f'数值文件路径无效 {path}')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError(f'数值文件摘要变化 {path}')
    dtype = item['dtype'].removeprefix('torch.')
    fmt = {'float32': 'f', 'float16': 'e', 'float64': 'd', 'bfloat16': 'H'}[dtype]
    if len(data) != item['size'] * struct.calcsize(fmt):
        raise ValueError(f'数值文件长度不符 {path}')
    values = [x[0] for x in struct.iter_unpack('<' + fmt, data)]
    if dtype == 'bfloat16':
        values = [struct.unpack('<f', struct.pack('<I', x << 16))[0] for x in values]
    if not allow_nan and any(math.isnan(x) for x in values):
        raise ValueError(f'预测向量含 NaN {path}')
    return values


def prediction(folder, root, ready, mapped, outputs, index):
    accepted = read(folder / f'accepted-{index}.json')
    frame = read(folder / f'prediction-{index}.json')
    keys = ('internal_id', 'output_index', 'forward_id')
    if (any(accepted[k] != frame[k] for k in keys) or frame['internal_id'] != mapped['internal_id']
            or frame['output_index'] != index or frame != read(folder / (frame['forward_id'] + '.json'))):
        raise ValueError(f'输出 {index} 的 worker、调度器或前向身份不符')
    values = prediction_values(folder, frame, accepted, ready, mapped, outputs, index)
    dump, calls, coverage = native_forward(root, ready, frame, frame['logits_indices'][0])
    path = folder / f'output-{index}.json'
    intent = read(folder / 'intent.json')
    cache = cache_evidence.from_frame(folder, frame, intent, ready, calls)
    write_json(path, {**accepted, **cache, 'client_request_id': intent['request_id'],
               'external_id': mapped['external_id'], **values, 'input_ids': frame['input_ids'],
               'positions': frame['positions'], 'position_axes': frame.get('position_axes', [frame['positions']]),
               'logits_indices': frame['logits_indices'], 'step': frame['step'],
               'batch_row': frame['batch_row'], 'rank': ready['rank'], 'calls': calls, 'layers': ready['layers'], 'coverage': coverage,
               **({'architecture': ready['architecture']} if 'architecture' in ready else {}),
               'native_metadata': [file_ref(dump / n, dump) for n in ('dump.json', 'construct.json')]})
    return file_ref(path, folder)


def prediction_values(folder, frame, accepted, ready, mapped, outputs, index):
    refs = {}
    names = ('raw', 'scores', 'probabilities') if 'sampler' in frame else ('raw', 'scores')
    for name in names:
        meta = frame[name]
        if meta['size'] != ready['vocab_size']:
            raise ValueError(f'输出 {index} 的 {name} 不是完整词表')
        refs[name] = {**meta, **file_ref(folder / meta['file'], folder)}
    raw, scores = vector(refs['raw']), vector(refs['scores'])
    candidate = max(range(len(scores)), key=scores.__getitem__)
    sample = {}
    if 'sampler' in frame:
        from .generation.sampling import decision
        candidate = frame['candidate']
        sample['sampling_decision'] = {**frame['sampler'], **decision(scores, vector(refs['probabilities']), candidate)}
    elif candidate != frame['candidate']:
        raise ValueError(f'输出 {index} 的选词分数 argmax 与 sampler 不符')
    prefix = correlate(mapped['prompt_ids'], outputs, index, frame, candidate, accepted['token_id'])
    return {**refs, **sample, 'prefix_ids': prefix, 'candidate': candidate, 'scores_changed': raw != scores}


def native_forward(root, ready, frame, row):
    dump = root / frame['dump']
    if dump.resolve() != dump or dump != root / 'dump' / 'native' / f"step{frame['step']}" / f"rank{ready['rank']}":
        raise ValueError('前向 msProbe step/rank 路径不符：' + frame['forward_id'])
    calls = native_calls(dump, ready, frame, row)
    coverage = None
    if 'coverage' in frame:
        from .adapters.coverage import verify
        coverage = verify(frame['coverage'], calls)
        if 'architecture' in ready:
            visible = {c['layer'] for c in calls if c['api']}
            coverage['layers_without_registered_api'] = [p for p in ready['layers'] if p not in visible]
    return dump, calls, coverage


def query(receipt, request_id, index, selection=None):
    result = read_bound(receipt)
    if result['request_id'] != request_id or not 0 <= index < len(result['outputs']):
        raise ValueError(f'请求 {request_id} 缺少输出 {index}')
    ready = verify_receipt(result)
    row = read_bound(result['outputs'][index])
    if row['output_index'] != index or row['token_id'] != result['token_ids'][index]:
        raise ValueError('索引引用的输出行身份错误')
    return verified_row(result, ready, row, request_id, result['outputs'][index], selection)


def verify_receipt(result):
    ready = read_bound(result['worker'])
    for name in ('derived_config', 'engine', 'sampling', 'response', 'engine_input'):
        read_bound(result[name])
    for name in ('frontend', 'engine_output'):
        if name in result: read_bound(result[name])
    for ref in result.get('forwards', []): read_bound(ref)
    for ref in result.get('http', {}).values():
        path = Path(ref['path'])
        if file_ref(path, path.parent)['sha256'] != ref['sha256']: raise ValueError('HTTP 原件摘要变化')
    return ready


def verified_row(result, ready, row, request_id, ref, selection=None):
    if row.get('architecture') != ready.get('architecture'):
        raise ValueError('层结构与已绑定 worker 清单不一致')
    if row['client_request_id'] != request_id:
        raise ValueError('索引引用的输出行身份错误')
    if row['output_index'] is not None:
        vector(row['raw'])
        vector(row['scores'])
    recheck = recheck_native(row, selection)
    if 'decode_cache' in row:
        preparation = read_bound(result['preparation'])
        read_bound(preparation['decode_cache'])
        cache_evidence.verify(Path(ref['path']).parent, row['decode_cache'], row)
    if 'timeline' in result:
        entry, = [item for item in result['timeline'] if item['row'] == ref]
        if entry['forward_id'] != row['forward_id'] or entry['output_index'] != row['output_index']:
            raise ValueError('时间线引用的前向身份错误')
        row = {**row, 'timeline_entry': entry}
    else:
        for extra in result.get('extra_forwards', []):
            verify_native(read_bound(extra))
    return {**row, **recheck, 'sampling_parameters': read_bound(result['sampling']), 'response_id': result['response_id']}


def query_forward(request):
    from .timeline import select_entry
    result = read_bound(request['source'])
    if result['request_id'] != request['request_id'] or 'timeline' not in result:
        raise ValueError('请求与全程采集回执不匹配')
    ready = verify_receipt(result)
    if 'view' in request:
        if 'prefix_reuse' in result:
            from .prefix.reader import timeline
            timeline(result, ready)
        for entry in result['timeline']:
            row = read_bound(entry['row'])
            if row['forward_id'] != entry['forward_id'] or row['output_index'] != entry['output_index']:
                raise ValueError('时间线行与已封存的前向不符')
        return {k: result[k] for k in ('request_id', 'timeline', 'token_ids', 'cache_capture', 'capture_totals',
                                       'cache_status', 'cache_errors', 'prefix_reuse') if k in result}
    if 'prompt_position' in request and request['prompt_position'] < result.get('prefix_reuse', {}).get('first_computed_position', 0):
        from .prefix.reader import lookup
        return lookup(result, request)
    item = select_entry(result['timeline'], request)
    ref = item['row']
    row = read_bound(ref)
    if row['forward_id'] != item['forward_id'] or row['output_index'] != item['output_index']:
        raise ValueError('时间线引用的前向身份错误')
    return selected(verified_row(result, ready, row, request['request_id'], ref, request), request)


def recheck_native(row, selection):
    selection = selection or {}
    layer = selected_layer(row, selection)
    if not layer or not selection.get('cache_snapshot', '').startswith('mla.'):
        verify_native(row)
        return {}
    owner_path = state_owners(row).get(layer, layer)
    calls = layer_calls(row, owner_path)
    report = {'layer': owner_path, 'scope': 'selected_module', 'status': 'complete', 'errors': [], 'invalid_calls': []}
    try:
        for ref in row['native_metadata']:
            read_bound(ref)
    except (ValueError, OSError) as error:
        report.update(status='incomplete', errors=[str(error)], invalid_calls=[c['call'] for c in calls])
        return {'native_recheck': report, 'native_metadata': []}
    if not calls:
        report.update(status='incomplete', errors=[f'msProbe 缺少模块调用 {owner_path}'])
    for call in calls:
        try:
            verify_tensors(row['forward_id'], call)
        except (ValueError, OSError) as error:
            report['invalid_calls'].append(call['call'])
            report['errors'].append(str(error))
            report['status'] = 'incomplete'
    return {'native_recheck': report}


def verify_native(row):
    for ref in row['native_metadata']: read_bound(ref)
    for call in row['calls']: verify_tensors(row['forward_id'], call)


def verify_tensors(forward_id, call):
    for args in call['tensors'].values():
        for item in args:
            if item.get('elements') == 0 and 0 in item['shape']:
                continue
            path = Path(item['path'])
            if file_ref(path, path.parent)['sha256'] != item['sha256']:
                raise ValueError(f"{forward_id} {call['call']} 张量摘要变化 {path}")


def selected_layer(row, request):
    layer = request.get('layer')
    if 'layer_index' in request:
        index = request['layer_index']
        paths = [path for path in row['layers'] if path.rsplit('.', 1)[-1] == str(index)]
        if len(paths) != 1:
            raise ValueError('所选层号无法唯一匹配当前结构清单')
        layer = paths[0]
    return layer


def selected(row, request):
    layer = selected_layer(row, request)
    api = request.get('api')
    call_layer = state_owners(row).get(layer, layer)
    calls = layer_calls(row, call_layer)
    if api:
        matches = [call for call in calls if call['call'].rsplit('.', 2)[0] == api and not call['module']]
        occurrence = request.get('occurrence', 0)
        if len(matches) <= occurrence:
            raise ValueError(f'此层缺少 API {api} 的第 {occurrence} 次调用')
        calls = [call for call in calls if call_layer and call['module'] == call_layer] + [matches[occurrence]]
    native = row.get('native_recheck', {})
    if native.get('status') == 'incomplete':
        calls = [c for c in calls if c['call'] not in native['invalid_calls']]
    result = {**row, 'calls': calls}
    result.update(decoded_predictions(row))
    if 'cache_snapshot' in request:
        result['cache_snapshot'] = selected_snapshot(row, request['cache_snapshot'], layer, request.get('occurrence', 0))
    return result


def selected_snapshot(row, name, layer=None, occurrence=0):
    from .layer_evidence import select_snapshot
    cache = row.get('decode_cache', {})
    if cache.get('format') == 2:
        item = select_snapshot(cache, name, layer, occurrence)
    else:
        kind = 'attention' if name.removeprefix('expected_').startswith(('key.', 'value.')) else 'gdn'
        module = cache.get(kind, {}).get('module', '')
        if layer and module != layer and not module.startswith(layer + '.'):
            raise ValueError('旧回执的快照不属于所选层')
        item = cache.get('snapshots', {}).get(name)
    if not item:
        raise ValueError('此输出没有所选缓存数值 ' + name)
    return decoded({**item, 'name': name, 'size': math.prod(item['shape'])}, allow_nan=True)


def state_owners(row):
    layers = (row.get('architecture') or {}).get('layers', ())
    return {s['module']: s['msprobe_module'] for block in layers for s in block.get('states', ())
            if 'msprobe_module' in s}


def layer_calls(row, layer):
    if not layer: return row['calls']
    names = set(row['layers']) | {call['module'] for call in row['calls'] if call['module']}
    if layer not in names:
        if layer in state_owners(row).values():
            return []
        visual = row.get('architecture', {}).get('other_stacks', {}).get('visual.blocks', [])
        if row.get('vision', {}).get('encoder_executed') and any(r['path'] == layer for r in visual):
            return []
        raise ValueError('此请求的前向缺少指定层 ' + layer)
    parents = {call['call']: call['parent'] for call in row['calls']}
    return [call for call in row['calls'] if owner(call['call'], parents, [layer]) == layer]


def decoded(item, *, allow_nan=False):
    values = vector(item, allow_nan=allow_nan)
    return {**item, 'values': [x if math.isfinite(x) else str(x) for x in values]}


def native_calls(dump, ready, frame, row):
    visual = []
    if frame.get('vision', {}).get('encoder_executed'):
        visual = [r['path'] for r in ready['architecture']['other_stacks']['visual.blocks']]
    calls = inventory(dump, [*ready['layers'], *visual], row, frame['positions'], require_layer_api='architecture' not in ready)
    for call in calls:
        if call['layer'] in visual or (visual and call['layer'] is None):
            for tensors in call['tensors'].values():
                for tensor in tensors:
                    tensor['slice'] = 'whole_tensor'
    return calls


def decoded_predictions(row):
    return {name: decoded(row[name]) for name in ('raw', 'scores', 'probabilities') if name in row}
