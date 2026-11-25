"""用调度顺序和引擎接受输出封存一次双卡请求，分别核验各个 worker。"""
from concurrent.futures import ThreadPoolExecutor
import re

from ....evidence import write_json
from ..adapters.state import read
from ..session_receipt import mapped_response, http_json
from ..tensors import file_ref
from .frames import rank_frame, missing_member, DATA_ERRORS
from .topology import device_identity


def check_dispatch(plan, expected, stop, prompt_size, length, seen):
    if any(not re.fullmatch(r'[a-f0-9]{32}', plan[key]) for key in ('forward_id', 'schedule_id')):
        raise ValueError('调度标识或前向标识无效')
    keys = ('ordinal', 'position', 'request_id', 'internal_id', 'phase', 'output_index')
    if tuple(plan[key] for key in keys) != expected or plan['forward_id'] in seen:
        raise ValueError('请求调度序号、前向身份或阶段不符')
    if plan['count'] <= 0 or stop > length:
        raise ValueError('调度输入范围无效')
    if plan['phase'] == 'decode' and plan['count'] != 1 or plan['phase'] == 'prefill' and stop > prompt_size:
        raise ValueError('调度区间跨阶段')


def scheduled_entries(folder, mapped, tokens, intent, interrupted=False):
    ordered = sorted(folder.glob('dispatch-*.json'), key=lambda p: int(p.stem.split('-')[1]))
    accepted = [read(folder / f'accepted-{i}.json') for i in range(len(tokens))]
    if len(list(folder.glob('accepted-*.json'))) != len(accepted):
        raise ValueError('引擎接受输出数量不符')
    prompt = mapped['prompt_ids']
    sequence = prompt + (tokens if interrupted else tokens[:-1])
    cursor = output = 0
    known = set()
    for order, path in enumerate(ordered):
        plan = read(path)
        stop = cursor + plan['count']
        phase = 'prefill' if cursor < len(prompt) else 'decode'
        check_dispatch(plan, (order, cursor, intent['request_id'], mapped['internal_id'], phase, output),
                       stop, len(prompt), len(sequence), known)
        if ('schedule', plan['schedule_id']) in known:
            raise ValueError('重复的调度标识')
        known.update([plan['forward_id'], ('schedule', plan['schedule_id'])])
        index = output if stop >= len(prompt) and output < len(tokens) else None
        item = {'ordinal': order, 'forward_id': plan['forward_id'], 'schedule_id': plan['schedule_id'],
                'internal_id': mapped['internal_id'], 'phase': phase, 'position_range': [cursor, stop],
                'input_ids': sequence[cursor:stop], 'prefix_ids': sequence[:stop], 'output_index': index,
                'token_id': None, 'dispatch': file_ref(path, folder)}
        if index is not None:
            token = accepted[index]
            if (token['forward_id'], token['internal_id'], token['output_index'], token['token_id']) != (
                    plan['forward_id'], mapped['internal_id'], index, tokens[index]):
                raise ValueError('逻辑前向与引擎接受的 Token 不符')
            item.update(token_id=tokens[index], accepted=file_ref(folder / f'accepted-{index}.json', folder))
            output += 1
        yield item, plan
        cursor = stop
    if (not interrupted and cursor != len(sequence)) or output != len(tokens):
        raise ValueError('调度时间线未覆盖全部输入或输出')


def collect(root, folder, intent, ready, mapped, tokens, interrupted=False):
    timeline, errors = [], []
    previous = [{}, {}]
    seen_steps = [set(), set()]

    def local(worker, entry, dispatch):
        rank = worker['rank']
        try:
            member = rank_frame(root, folder, worker, entry, dispatch, intent, mapped, tokens, previous[rank])
            if member['step'] in seen_steps[rank]:
                raise ValueError('rank 内重复 msProbe step')
            seen_steps[rank].add(member['step'])
            return member
        except DATA_ERRORS as error:
            previous[rank].clear()
            return missing_member(root, folder, worker, entry, dispatch, intent, error)

    # 两套独立原生文件可同时核验；下一前向必须等待本前向的每卡状态承接检查。
    with ThreadPoolExecutor(max_workers=2) as pool:
        for entry, dispatch in scheduled_entries(folder, mapped, tokens, intent, interrupted):
            jobs = [pool.submit(local, worker, entry, dispatch) for worker in ready['rank_workers']]
            members = [job.result() for job in jobs]
            compare_position_axes(members)
            for member, worker in zip(members, ready['rank_workers'], strict=True):
                member['device'] = device_identity(worker)
                errors.extend(f"{entry['forward_id']} rank{member['rank']}: {e}" for e in member['errors'])
            timeline.append({**entry, 'ranks': members})
    return timeline, errors


def seal_parallel(request, authority, handoff, root, folder, intent, ready):
    mapped, engine, response = mapped_response(folder, request)
    sampling = http_json(request['http']['request'])
    tokens = accepted_tokens(folder, engine, response)
    timeline, errors = collect(root, folder, intent, ready, mapped, tokens, not engine.get('finished', True))
    errors.extend(ready.get('service_errors', []))
    errors.extend(str(read(p)['error']) for p in folder.rglob('error-*.json'))
    totals = [{key: sum(entry['ranks'][rank].get(key, 0) for entry in timeline)
               for key in ('msprobe_bytes', 'snapshot_bytes', 'observer_seconds', 'msprobe_window_seconds')} for rank in range(2)]
    check_budgets(intent, ready, timeline, totals, errors)
    from ..generation.receipt import generation_evidence
    generation = generation_evidence(folder, sampling, response, timeline, errors, request['http'])
    refs, elapsed, timing_source = frontend_metadata(folder, generation)
    path = folder / 'capture-receipt.json'
    status = 'incomplete' if errors else 'complete'
    result = {**generation, 'inference_status': 'completed' if engine.get('finished', True) else 'interrupted', 'tensor_parallel_size': 2, 'request_id': intent['request_id'], 'token_ids': tokens,
              'output_rank': ready['output_rank'], 'timeline': timeline, 'cache_status': status, 'cache_errors': errors,
              'capture_totals': {'by_rank': totals, 'request_wall_seconds': elapsed, 'wall_time_source': timing_source,
                                 'msprobe_bytes': sum(r['msprobe_bytes'] for r in totals),
                                 'snapshot_bytes': sum(r['snapshot_bytes'] for r in totals)},
              'workers': ready.get('worker_refs') or [file_ref(root / f'worker-rank{i}.json', root) for i in range(2)],
              'executor': file_ref(root / 'executor.json', root), 'engine': file_ref(root / 'engine.json', root),
              'preparation': handoff['worker_capture']['receipt'], 'derived_config': authority['derived_config'],
              'source': authority['source'], 'sampling_parameters': sampling, 'response_id': response['id'],
              'http': request['http'], 'sample_key': intent['sample_key'], 'native_id': intent['native_id'], **refs}
    if 'cache_capture' in intent:
        result['cache_capture'] = intent['cache_capture']
    write_json(path, result)
    return {'status': 'completed' if status == 'complete' else status, 'cache_status': status, 'cache_errors': errors,
            'trace_id': request['trace_id'], 'sample_key': intent['sample_key'], 'native_id': intent['native_id'],
            'receipt': file_ref(path, folder), 'token_count': len(tokens), 'capture_totals': result['capture_totals']}



def check_budgets(intent, ready, timeline, totals, errors):
    if 'cache_capture' not in intent:
        return
    from ..adapters.parallel import share
    from types import SimpleNamespace
    for worker, total in zip(ready['rank_workers'], totals, strict=True):
        rank = worker['rank']
        limit = share(SimpleNamespace(rank=rank, world_size=2), intent['cache_capture']['max_request_snapshot_bytes'])
        if total['snapshot_bytes'] > limit:
            errors.append(f'rank{rank} 请求实际快照字节超过分配额度')
        cap = worker['decode_cache']['max_snapshot_bytes']
        for entry in timeline:
            local = entry['ranks'][rank]
            if local.get('snapshot_bytes', 0) > cap:
                reason = f"{entry['forward_id']} rank{rank} 单前向实际快照字节超过分配额度"
                local['errors'].append(reason)
                local['status'] = 'incomplete'
                errors.append(reason)


def accepted_tokens(folder, engine, response):
    tokens = engine['token_ids']
    delivery = response.get('delivery', {})
    delivered = response['choices'][0].get('token_ids')
    if delivery and not delivery['complete']:
        accepted = sorted(folder.glob('accepted-*.json'), key=lambda p: int(p.stem.split('-')[-1]))
        tokens = [read(p)['token_id'] for p in accepted]
        if engine['token_ids'] != tokens[:len(engine['token_ids'])]:
            raise ValueError('SSE engine output differs from scheduler')
    elif not delivery and delivered != tokens:
        raise ValueError('引擎与 HTTP 输出序列不一致')
    return tokens


def frontend_metadata(folder, generation):
    refs = {key: file_ref(folder / name, folder) for key, name in (
        ('engine_input', 'request.json'), ('frontend', 'frontend.json'), ('engine_output', 'engine-output.json'))
            if (folder / name).is_file()}
    timing = read(folder / 'frontend-end.json')
    elapsed = timing.get('request_wall_seconds')
    timing_source = 'frontend_request'
    if elapsed is None:
        elapsed = (folder / 'frontend-end.json').stat().st_mtime - (folder / 'request.json').stat().st_mtime
        timing_source = 'engine_input_to_frontend_end'
    if 'delivery' in generation:
        generation['delivery']['frontend_stream_exhausted'] = timing.get('stream_exhausted')
        generation['delivery']['frontend_engine_result_observed'] = 'engine_output' in refs
    return refs, elapsed, timing_source


def compare_position_axes(members):
    axes = [member['position_axes'] for member in members if 'position_axes' in member]
    if len(axes) == 2 and axes[0] != axes[1]:
        for member in members:
            member['status'] = 'incomplete'
            member['errors'].append('两个 rank 实收的 M-RoPE 位置轴不同')
