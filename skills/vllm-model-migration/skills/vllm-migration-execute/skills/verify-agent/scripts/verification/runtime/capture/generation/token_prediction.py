"""单卡同步 Eager 的纯文本贪心预测。归一化概率来自实际选词前分数。"""
import math
from pathlib import Path

from ....evidence import write_json
from ..adapters.state import read
from ..index import correlate, vector
from ..tensors import file_ref


def validate_selection(request):
    if (request.get('profile') != 'token_prediction' or request['operation'] != 'arm'
            or 'cache_output_index' in request or 'cache_capture' in request):
        raise ValueError('token_prediction 须在 arm 单独选择，不支持缓存快照')


def verify_worker(ready):
    if ready.get('token_prediction') is not True or ready.get('mode') != 'eager' or 'rank_workers' in ready:
        raise ValueError('需要支持 token_prediction 的单卡同步 Eager worker')


def portable_vector(folder, frame, name, vocab_size):
    meta = frame[name]
    if meta['size'] != vocab_size or Path(meta['file']).name != meta['file']:
        raise ValueError('预测向量词表长度或文件名不符')
    disk_ref = file_ref(folder / meta['file'], folder)
    values = vector({**disk_ref, **meta})
    return {'path': meta['file'], 'sha256': disk_ref['sha256'],
            'dtype': meta['dtype'].removeprefix('torch.'), 'size': vocab_size}, values


def prediction(folder, mapped, tokens, index, ready):
    frame = read(folder / f'prediction-{index}.json')
    accepted = read(folder / f'accepted-{index}.json')
    identity = ('forward_id', 'output_index', 'internal_id')
    if (frame.get('profile') != 'token_prediction' or frame['output_index'] != index
            or frame['internal_id'] != mapped['internal_id'] or any(frame[k] != accepted[k] for k in identity)):
        raise ValueError(f'第 {index} 步的 worker 与实际接受事件身份不符')
    if frame != read(folder / (frame['forward_id'] + '.json')):
        raise ValueError('采样行与原始前向不同')
    end = read(folder / ('end-' + frame['forward_id'] + '.json'))
    if end['internal_id'] != mapped['internal_id'] or end['forward_id'] != frame['forward_id']:
        raise ValueError('前向回执身份有误')
    correlate(mapped['prompt_ids'], tokens, index, frame, frame['candidate'], accepted['token_id'])
    raw, _ = portable_vector(folder, frame, 'raw', ready['vocab_size'])
    probs, values = portable_vector(folder, frame, 'processed_probs', ready['vocab_size'])
    if not all(0 <= p <= 1 for p in values) or not math.isclose(math.fsum(values), 1., rel_tol=1e-6):
        raise ValueError('处理后的概率不是有效的全词表分布')
    if not 0 <= tokens[index] < len(values) or values[tokens[index]] != max(values):
        raise ValueError('实际接受 Token 与贪心分布不符')
    return raw, probs


def bound_generation(folder, request):
    from ..session_receipt import http_json, mapped_response
    payload = http_json(request['http']['request'])
    observed = read(folder / 'frontend-payload-digest.json')
    if observed['http_sha256'] != request['http']['request']['sha256']:
        raise ValueError('提交的 HTTP 原件与真实前端接收的请求不同')
    if (payload.get('stream') or payload.get('n', 1) != 1 or payload.get('temperature') != 0
            or payload.get('return_token_ids') is not True):
        raise ValueError('Token 采集需要显式贪心、单输出、非流式和 return_token_ids=true')
    mapped, engine, response = mapped_response(folder, request)
    if mapped.get('mm_features') or not engine.get('finished') or not engine['finish_reason']:
        raise ValueError('需要已结束的纯文本请求')
    choice, = response['choices']
    if (choice.get('token_ids') != engine['token_ids'] or choice['finish_reason'] != engine['finish_reason']
            or read(folder / 'frontend-end.json')['trace_id'] != request['trace_id']):
        raise ValueError('HTTP 返回、引擎实际接受的 Token、停止原因或 trace 不符')
    return mapped, engine, choice.get('stop_reason')


def termination(reason, trigger):
    if reason != 'stop':
        return reason
    if type(trigger) is int:
        return 'stop_token:' + str(trigger)
    if isinstance(trigger, str) and trigger:
        return 'stop_string:' + trigger
    return 'unknown_stop'


def seal(request, authority, handoff, folder, intent, ready):
    from ..session_receipt import completed_forwards
    verify_worker(ready)
    errors = list(folder.glob('error-*.json'))
    if errors:
        raise ValueError(read(errors[0])['error'])
    mapped, engine, stop_trigger = bound_generation(folder, request)
    completed_forwards(folder)
    tokens = engine['token_ids']
    if len(list(folder.glob('accepted-*.json'))) != len(tokens):
        raise ValueError('调度器接受数量与最终响应不符')
    raw, probs = [], []
    for index in range(len(tokens)):
        logits, distribution = prediction(folder, mapped, tokens, index, ready)
        raw.append(logits)
        probs.append(distribution)
    sample_path = folder / 'token-sample.json'
    write_json(sample_path, {'evidence_kind': 'runtime', 'sample_id': intent['sample_key'],
                            'prompt_token_ids': mapped['prompt_ids'], 'output_token_ids': tokens,
                            'stop_reason': termination(engine['finish_reason'], stop_trigger),
                            'raw_logits': raw, 'processed_probs': probs})
    sample = file_ref(sample_path, folder)
    path = folder / 'capture-receipt.json'
    receipt = {'profile': 'token_prediction', 'trace_id': request['trace_id'], 'sample_key': intent['sample_key'],
               'native_id': intent['native_id'], 'source': authority['source'], 'http': request['http'],
               'preparation': handoff['worker_capture']['receipt'], 'token_sample': sample,
               'finish_reason': engine['finish_reason'], 'stop_trigger': stop_trigger,
               'engine_input': file_ref(folder / 'request.json', folder),
               'engine_output': file_ref(folder / 'engine-output.json', folder)}
    write_json(path, receipt)
    return {'status': 'completed', 'trace_id': request['trace_id'], 'sample_key': intent['sample_key'],
            'native_id': intent['native_id'], 'token_count': len(tokens), 'token_sample': sample,
            'receipt': file_ref(path, folder)}
