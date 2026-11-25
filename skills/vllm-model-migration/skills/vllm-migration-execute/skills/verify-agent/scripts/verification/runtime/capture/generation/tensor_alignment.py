"""封存单样本目标前向和完整实物清单。只以实测 Token 和重验文件交付。"""
from math import fsum, isclose
from pathlib import Path
import re

from ....evidence import write_json
from ..adapters.alignment.manifest import write_native_index
from ..adapters.alignment.plan import read_plan
from ..adapters.state import read
from ..index import correlate
from ..tensors import file_ref
from .token_prediction import bound_generation, portable_vector


def validate_selection(request):
    ref = request.get('tensor_plan')
    if request['operation'] != 'arm' or 'cache_capture' in request or 'cache_output_index' in request:
        raise ValueError('定向张量采集须在 arm 独立选择')
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('定向采集需要固定的 tensor_plan 引用')
    if not Path(ref['path']).is_absolute() or not re.fullmatch('[0-9a-f]{64}', ref['sha256']):
        raise ValueError('tensor_plan 路径或摘要无效')


def snapshot_plan(request, folder):
    ref = request['tensor_plan']
    raw, plan = read_plan(ref['path'], ref['sha256'])
    if plan['case']['sample_id'] != request['sample_key']:
        raise ValueError('计划的样本身份与 arm 操作不一致')
    # Save the exact bound bytes on the capture mount shared by the worker.
    (folder / 'tensor-plan.json').write_bytes(raw)


def verify_worker(ready):
    expected = {'tensor_alignment': True, 'synchronous': True, 'rank': 0, 'mode': 'eager'}
    if any(type(ready.get(k)) is not type(v) or ready[k] != v for k, v in expected.items()) or 'rank_workers' in ready:
        raise ValueError('定向张量采集需要已适配的单卡同步 Eager worker')


def target_prediction(folder, mapped, tokens, plan, size):
    idx = plan['position'] - 1
    if idx >= len(tokens):
        raise ValueError('请求在目标输出前结束')
    frame = read(folder / f'prediction-{idx}.json')
    accepted = read(folder / f'accepted-{idx}.json')
    if not frame.get('versions') or frame['versions'] != plan.get('runtime_versions', {}).get('npu'):
        raise ValueError('实际前向的软件版本与冻结的 NPU 计划不同')
    if not frame.get('targeted') or frame.get('profile') != 'tensor_alignment':
        raise ValueError('实际接受的 Token 缺少定向采集前向')
    if (frame['internal_id'] != mapped['internal_id'] or frame['output_index'] != idx
            or any(frame[k] != accepted[k] for k in ('internal_id','output_index','forward_id'))):
        raise ValueError('实际调度接受事件与 worker 不一致')
    check_frame_files(folder, frame)
    prefix = correlate(mapped['prompt_ids'], tokens, idx, frame, frame['candidate'], accepted['token_id'])
    if prefix != plan['prefix_token_ids'] or mapped['prompt_ids'] != plan['prompt_token_ids']:
        raise ValueError('模型实际消费的目标共享前文与计划不一致')
    return frame, prediction_vectors(folder, frame, size, tokens[idx])


def check_frame_files(folder, frame):
    fid = frame['forward_id']
    if not re.fullmatch('[0-9a-f]{32}', fid):
        raise ValueError('目标前向标识格式无效')
    if frame != read(folder / (fid + '.json')) or read(folder / 'target-frame.json')['forward_id'] != fid:
        raise ValueError('目标调用回执未绑定实际前向')
    end = read(folder / ('end-' + fid + '.json'))
    if (end['internal_id'], end['forward_id'], end.get('tensors')) != (frame['internal_id'], fid, frame['tensors']):
        raise ValueError('目标前向闭合事件和入选张量不一致')


def prediction_vectors(folder, frame, size, token):
    raw, _ = portable_vector(folder, frame, 'raw', size)
    probs, values = portable_vector(folder, frame, 'processed_probs', size)
    if not all(0 <= p <= 1 for p in values) or not isclose(fsum(values), 1., rel_tol=1.e-6):
        raise ValueError('处理后的全词表概率无效')
    if not 0 <= token < size or values[token] != max(values):
        raise ValueError('实际接受的 Token 与 greedy 预测不符')
    return {'raw_logits': raw, 'processed_probs': probs, 'token_id': token}


def audit(folder, plan, frame):
    expected = {(c['id'], s['id']) for c in plan['calls'] for s in c['slots']}
    seen, files, rows = set(), set(), []
    for row in frame['tensors']:
        key, name = (row['call'], row['slot']), row['path']
        if key not in expected or key in seen or name in files or not re.fullmatch(r'target-[0-9]+\.pt', name):
            raise ValueError('张量清单包含重复、计划外调用或文件')
        if file_ref(folder / name, folder)['sha256'] != row['sha256']:
            raise ValueError('定向张量摘要已变化')
        seen.add(key)
        files.add(name)
        rows.append(row)
    if payload_paths(folder, {'.pt', '.npy', '.npz', '.pth', '.part', '.safetensors'}) != files:
        raise ValueError('张量实物集合与封存清单不一致')
    missing = [{'call': c, 'slot': s} for c, s in sorted(expected - seen)]
    return rows, missing


def native_index(folder, bundle, frame, ready):
    execution = {key: frame[key] for key in ('internal_id', 'output_index', 'positions', 'input_ids', 'logits_indices', 'versions')}
    execution.update(trace_id=bundle['trace_id'], phase=bundle['phase'], rank=ready['rank'], mode=ready['mode'],
                     synchronous=ready['synchronous'], world_size=1, batch_size=1)
    path = folder / (frame['forward_id'] + '.json')
    ref = {**file_ref(path, folder), 'path': path.name}
    return write_native_index(folder, 'npu', bundle['plan_sha256'], frame['forward_id'], execution, bundle['tensors'], ref)


def payload_paths(folder, suffixes):
    return {str(p.relative_to(folder)) for p in folder.rglob('*') if p.suffix in suffixes}


def audit_payload_files(folder, frame, prediction):
    vectors = {prediction[key]['path'] for key in ('raw_logits', 'processed_probs')}
    if payload_paths(folder, {'.bin'}) != vectors:
        raise ValueError('目标预测向量与实际二进制文件集合不一致')
    step = frame['native_step']
    if type(step) is not int or step < 0:
        raise ValueError('缺少实际 msProbe 前向步')
    native = folder.parent / 'dump' / 'native' / f'step{step}' / 'rank0'
    if not native.is_dir() or payload_paths(native, {'.pt', '.npy', '.npz', '.pth', '.bin', '.safetensors'}):
        raise ValueError('定向模式的 msProbe 输出目录缺失或意外写入原生 Tensor')


def seal(request, authority, handoff, folder, intent, ready):
    from ..session_receipt import completed_forwards
    verify_worker(ready)
    errors = list(folder.glob('error-*.json'))
    if errors:
        raise ValueError(read(errors[0])['error'])
    mapped, engine, _ = bound_generation(folder, request)
    completed_forwards(folder)
    digest = intent['tensor_plan']['sha256']
    _, plan = read_plan(folder / 'tensor-plan.json', digest)
    if read(folder / 'target-frame.json')['plan_sha256'] != digest:
        raise ValueError('前向绑定的采集计划已变化')
    frame, prediction = target_prediction(folder, mapped, engine['token_ids'], plan, ready['vocab_size'])
    rows, missing = audit(folder, plan, frame)
    audit_payload_files(folder, frame, prediction)
    status = 'incomplete' if missing or frame['tensor_errors'] else 'completed'
    bundle = {key: plan[key] for key in ('case', 'position', 'prefix_token_ids', 'prompt_token_ids', 'model_identity')}
    bundle.update(format='tensor-bundle-v1', hardware='npu', evidence_kind='runtime', rank=0, status=status,
                  plan_sha256=digest, forward_id=frame['forward_id'], trace_id=intent['trace_id'],
                  phase='decode' if plan['position'] > 1 else 'prefill', prediction=prediction,
                  tensors=rows, missing=missing, errors=frame['tensor_errors'], capture_seconds=frame.get('capture_seconds'),
                  tensor_bytes=sum((folder / row['path']).stat().st_size for row in rows),
                  prediction_bytes=sum((folder / prediction[k]['path']).stat().st_size for k in ('raw_logits','processed_probs')))
    bundle['native_index'] = native_index(folder, bundle, frame, ready)
    dest = folder / 'tensor-bundle.json'
    write_json(dest, bundle)
    receipt = {'profile':'tensor_alignment', 'tensor_bundle':file_ref(dest, folder), 'source':authority['source'],
               'http':request['http'], 'native_id':intent['native_id'], 'trace_id':intent['trace_id'],
               'sample_key':intent['sample_key'], 'plan':file_ref(folder / 'tensor-plan.json', folder),
               'preparation':handoff['worker_capture']['receipt']}
    path = folder / 'capture-receipt.json'
    write_json(path, receipt)
    return {**receipt, 'receipt':file_ref(path, folder), 'status':status}
