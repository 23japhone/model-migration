"""按请求与输出序号联结 prompt、前向、采样和实际接收证据。"""
import math
from pathlib import Path
import struct

from ...evidence import read_bound, write_json
from .adapters.state import read
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


def vector(item):
    path = Path(item['path'])
    if file_ref(path, path.parent)['sha256'] != item['sha256']:
        raise ValueError(f'预测向量摘要变化 {path}')
    dtype = item['dtype'].removeprefix('torch.')
    fmt = {'float32': 'f', 'float16': 'e', 'float64': 'd', 'bfloat16': 'H'}[dtype]
    data = path.read_bytes()
    if len(data) != item['size'] * struct.calcsize(fmt):
        raise ValueError(f'预测向量长度不符 {path}')
    values = [x[0] for x in struct.iter_unpack('<' + fmt, data)]
    if dtype == 'bfloat16':
        values = [struct.unpack('<f', struct.pack('<I', x << 16))[0] for x in values]
    if any(math.isnan(x) for x in values):
        raise ValueError(f'预测向量含 NaN {path}')
    return values


def prediction(folder, root, ready, mapped, outputs, index):
    accepted = read(folder / f'accepted-{index}.json')
    frame = read(folder / f'prediction-{index}.json')
    keys = ('internal_id', 'output_index', 'forward_id')
    if (any(accepted[k] != frame[k] for k in keys) or frame['internal_id'] != mapped['internal_id']
            or frame['output_index'] != index or frame != read(folder / (frame['forward_id'] + '.json'))):
        raise ValueError(f'输出 {index} 的 worker、调度器或前向身份不符')
    refs = {}
    for name in ('raw', 'scores'):
        meta = frame[name]
        if meta['size'] != ready['vocab_size']:
            raise ValueError(f'输出 {index} 的 {name} 不是完整词表')
        refs[name] = {**meta, **file_ref(folder / meta['file'], folder)}
    raw, scores = vector(refs['raw']), vector(refs['scores'])
    candidate = max(range(len(scores)), key=scores.__getitem__)
    if candidate != frame['candidate']:
        raise ValueError(f'输出 {index} 的选词分数 argmax 与 sampler 不符')
    prefix = correlate(mapped['prompt_ids'], outputs, index, frame, candidate, accepted['token_id'])
    dump, calls, coverage = native_forward(root, ready, frame, frame['logits_indices'][0])
    path = folder / f'output-{index}.json'
    write_json(path, {**accepted, 'client_request_id': read(folder / 'intent.json')['request_id'],
               'external_id': mapped['external_id'], 'prefix_ids': prefix, 'input_ids': frame['input_ids'],
               'positions': frame['positions'], 'position_axes': frame.get('position_axes', [frame['positions']]),
               'logits_indices': frame['logits_indices'], 'step': frame['step'],
               'batch_row': frame['batch_row'], 'rank': ready['rank'], 'candidate': candidate,
               'scores_changed': raw != scores, **refs, 'calls': calls, 'layers': ready['layers'], 'coverage': coverage,
               'native_metadata': [file_ref(dump / n, dump) for n in ('dump.json', 'construct.json')]})
    return file_ref(path, folder)


def native_forward(root, ready, frame, row):
    dump = root / frame['dump']
    if dump.resolve() != dump or dump != root / 'dump' / 'native' / f"step{frame['step']}" / 'rank0':
        raise ValueError('前向 msProbe step/rank 路径不符：' + frame['forward_id'])
    calls = inventory(dump, ready['layers'], row, frame['positions'])
    coverage = None
    if 'coverage' in frame:
        from .adapters.coverage import verify
        coverage = verify(frame['coverage'], calls)
    return dump, calls, coverage


def query(receipt, request_id, index):
    result = read_bound(receipt)
    if result['request_id'] != request_id or not 0 <= index < len(result['outputs']):
        raise ValueError(f'请求 {request_id} 缺少输出 {index}')
    for name in ('derived_config', 'worker', 'engine', 'sampling', 'response', 'engine_input'):
        read_bound(result[name])
    for name in ('frontend', 'engine_output'):
        if name in result: read_bound(result[name])
    for ref in result.get('forwards', []): read_bound(ref)
    for ref in result.get('http', {}).values():
        path = Path(ref['path'])
        if file_ref(path, path.parent)['sha256'] != ref['sha256']: raise ValueError('HTTP 原件摘要变化')
    row = read_bound(result['outputs'][index])
    if (row['client_request_id'] != request_id or row['output_index'] != index
            or row['token_id'] != result['token_ids'][index]):
        raise ValueError('索引引用的输出行身份错误')
    vector(row['raw'])
    vector(row['scores'])
    verify_native(row)
    for ref in result.get('extra_forwards', []): verify_native(read_bound(ref))
    return {**row, 'sampling_parameters': read_bound(result['sampling']), 'response_id': result['response_id']}


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


def selected(row, request):
    layer = request.get('layer')
    api = request.get('api')
    calls = layer_calls(row, layer)
    if api:
        matches = [call for call in calls if call['call'].rsplit('.', 2)[0] == api and not call['module']]
        occurrence = request.get('occurrence', 0)
        if len(matches) <= occurrence:
            raise ValueError(f'此层缺少 API {api} 的第 {occurrence} 次调用')
        calls = [call for call in calls if layer and call['module'] == layer] + [matches[occurrence]]
    return {**row, 'calls': calls, 'raw': decoded(row['raw']), 'scores': decoded(row['scores'])}


def layer_calls(row, layer):
    if not layer: return row['calls']
    names = set(row['layers']) | {call['module'] for call in row['calls'] if call['module']}
    if layer not in names: raise ValueError('此请求的前向缺少指定层 ' + layer)
    parents = {call['call']: call['parent'] for call in row['calls']}
    return [call for call in row['calls'] if owner(call['call'], parents, [layer]) == layer]


def decoded(item):
    return {**item, 'values': [x if math.isfinite(x) else str(x) for x in vector(item)]}
