"""客户 GPU 包的真实调用索引、目标预测向量和采集开销。"""
import hashlib
import json


def write_json(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def vector(folder, name, tensor):
    import torch
    if tensor.ndim != 2 or tensor.shape[0] != 1:
        raise ValueError('目标预测应有且仅有一个有效词表行')
    raw = tensor[0].contiguous().view(torch.uint8).numpy().tobytes()
    path = folder / (name + '.bin')
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': path.name, 'dtype': str(tensor.dtype).removeprefix('torch.'),
            'size': tensor.shape[1], 'sha256': hashlib.sha256(raw).hexdigest()}


def publish(capture, accepted):
    from ..runtime.capture.adapters.alignment.manifest import write_native_index
    folder, plan, source = capture.folder, capture.plan, capture.source
    phase = 'prefill' if plan['position'] == 1 else 'decode'
    execution = {**capture.frame, 'trace_id': capture.trace, 'internal_id': capture.internal,
                 'phase': phase, 'versions': capture.versions, 'mode': 'eager', 'rank': 0, 'world_size': 1, 'synchronous': True, 'batch_size': 1}
    rows = capture.gate.rows
    native = write_native_index(folder, 'gpu', source['sha256'], capture.forward_id, execution, rows)
    pred = {'raw_logits': vector(folder, 'raw', capture.raw),
            'processed_probs': vector(folder, 'probs', capture.probs), 'token_id': accepted}
    planned = {(call['id'], slot['id']) for call in plan['calls'] for slot in call['slots']}
    actual = {(row['call'], row['slot']) for row in rows}
    bundle = {key: plan[key] for key in ('case', 'position', 'model_identity', 'prompt_token_ids', 'prefix_token_ids')}
    kind = 'runtime' if str(capture.runner.device).partition(':')[0] == 'cuda' else 'synthetic'
    bundle.update(format='tensor-bundle-v1', hardware='gpu', evidence_kind=kind, phase=phase,
                  rank=0, trace_id=capture.trace, forward_id=capture.forward_id, plan_sha256=source['sha256'],
                  native_index=native, prediction=pred, tensors=rows, errors=capture.gate.errors,
                  status='complete' if actual == planned and not capture.gate.errors else 'incomplete',
                  capture_seconds=capture.capture_seconds,
                  prediction_bytes=sum((folder / pred[k]['path']).stat().st_size for k in ('raw_logits','processed_probs')),
                  tensor_bytes=sum((folder / item['path']).stat().st_size for item in rows))
    write_json(folder / 'tensor-bundle.json', bundle)
