"""重读目标 worker 的调用索引、有效预测行和定向采集目录。"""
from pathlib import Path
import re

from ..evidence import bound
from .artifacts import tensor_path
from .contract import natural, numbers, text


def relative_json(bundle, ref):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('原生索引引用须包含路径与摘要')
    path = tensor_path(bundle, ref)
    return bound({'path': str(path), 'sha256': ref['sha256']}).value


def target_rows(execution, prefix):
    indices = execution['logits_indices']
    positions, ids = execution['positions'], execution['input_ids']
    if not numbers(positions) or not numbers(ids) or not positions or len(ids) != len(positions):
        raise ValueError('实际输入行或位置不完整')
    if positions != list(range(positions[0], positions[0] + len(positions))) or positions[-1] >= len(prefix):
        raise ValueError('前向有效输入范围与计划不符')
    if ids != prefix[positions[0]:positions[-1]+1]:
        raise ValueError('前向实际消费的 Token ID 不符')
    if not numbers(indices) or len(indices) != 1 or indices[0] >= len(positions) or positions[indices[0]] != len(prefix)-1:
        raise ValueError('本次有效预测行不能定位到指定输出')


def execution_origin(bundle, plan, native):
    data = bundle.value
    frame = native['execution']
    versions = plan['runtime_versions'][data['hardware']]
    if not isinstance(versions, dict) or set(versions) != {'torch', 'vllm', 'msprobe'}:
        raise ValueError('计划未冻结该端 PyTorch、vLLM 和 msProbe 版本')
    for package in versions:
        text(versions[package], 'runtime_versions.' + package)
    if frame.get('versions') != versions:
        raise ValueError('worker 实际软件版本与该端冻结版本不同')
    for key, expected in {'mode': 'eager', 'batch_size': 1, 'rank': 0, 'world_size': 1,
                          'synchronous': True, 'phase': data['phase'], 'output_index': data['position']-1}.items():
        actual = frame.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError('实际前向条件不匹配：' + key)
    text(frame.get('internal_id'), 'engine request_id')
    text(data.get('trace_id'), 'trace_id')
    if frame.get('trace_id') != data['trace_id']:
        raise ValueError('原始请求 trace_id 不匹配')
    target_rows(frame, plan['prefix_token_ids'])
    if 'native_frame' in native:
        origin = relative_json(bundle, native['native_frame'])
        expected = {'forward_id': data['forward_id'], 'internal_id': frame['internal_id'],
                    'output_index': frame['output_index'], 'tensors': native['tensors'],
                    **{key: frame[key] for key in ('positions', 'input_ids', 'logits_indices')}}
        if any(origin.get(k) != v for k, v in expected.items()):
            raise ValueError('worker 原始前向文件与采集索引不符')


def scalar_arguments(row, target):
    for key in ('arguments_complete', 'scalar_args'):
        if key in target and (key not in row or row[key] != target[key]):
            raise ValueError('原生调用的已核验参数与计划声明不同：' + key)


def native_call(row, call, slot, hardware):
    target = call[hardware]
    name = row.get('native_name', '')
    scalar_arguments(row, target)
    if row.get('argument') != slot[hardware] or row.get('occurrence') != call['occurrence']:
        raise ValueError('原生张量参数位置或调用序号不符')
    if not natural(row.get('ordinal')) or row['ordinal'] < 1:
        raise ValueError('缺少原生调用的实际执行序号')
    if call['kind'] == 'api':
        pattern = re.escape(target['name']) + r'\.\d+\.forward'
        if row.get('native_parent') != target['owner'] or not re.fullmatch(pattern, name):
            raise ValueError('原生 API 名称或父模块不同')
        return
    owner, physical = target['owner'], target['name']
    if owner and physical != owner and not physical.startswith(owner + '.'):
        physical = owner + '.' + physical
    pattern = r'Module\.' + re.escape(physical) + r'\.[^.]+\.forward\.\d+'
    if not re.fullmatch(pattern, name):
        raise ValueError('原生模块名称不同')


def disk_inventory(bundle):
    root = Path(bundle.ref['path']).parent
    records = bundle.value['tensors']
    listed = {tensor_path(bundle, r) for r in records}
    if len(listed) != len(records):
        raise ValueError('多条原生张量记录引用相同的文件')
    on_disk = {path for path in root.rglob('*') if path.suffix.lower() in ('.pt', '.npy', '.npz', '.pth', '.safetensors')}
    if on_disk != listed:
        raise ValueError('定向采集目录与原生张量清单不同')


def verify(bundle, plan):
    data = bundle.value
    if data.get('evidence_kind') == 'synthetic':
        return
    if data.get('evidence_kind') != 'runtime':
        raise ValueError('须明确标记 runtime 或 synthetic 采集来源')
    native = relative_json(bundle, data['native_index'])
    if native.get('format') != 'tensor-native-index-v1':
        raise ValueError('原生调用索引格式不符')
    for key in ('hardware', 'plan_sha256', 'forward_id', 'rank', 'tensors'):
        if native.get(key) != data[key]:
            raise ValueError('原生索引与本次张量包不同：' + key)
    execution_origin(bundle, plan, native)
    targets = {(c['id'], s['id']): (c, s) for c in plan['calls'] for s in c['slots']}
    for row in native['tensors']:
        call, slot = targets[row['call'], row['slot']]
        native_call(row, call, slot, data['hardware'])
    disk_inventory(bundle)
