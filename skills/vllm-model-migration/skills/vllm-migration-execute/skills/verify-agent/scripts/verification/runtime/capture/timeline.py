"""以已接受的输出和实际前向构建冷前缀请求的顺序索引。"""
from pathlib import Path

from ...evidence import read_bound
from .adapters.state import read


def gdn_continuity(cache, previous, position):
    errors = []
    current = {}
    for layer in cache.get('layers', []):
        for call in layer['invocations']:
            for state in call['states']:
                if state['kind'] != 'gdn' or state.get('errors'):
                    continue
                event = state.get('event', {})
                for name in ('conv', 'ssm'):
                    key = (state['module'], name)
                    part = event.get(name, {})
                    if position and key in previous and part.get('digest_before') != previous[key]:
                        errors.append(f"{state['module']} {name} 前态与前一块终态不符")
                    if 'digest_after' in part:
                        current[key] = part['digest_after']
                if event.get('observation') == 'prefill_gdn' and event['has_initial_state'] != bool(position):
                    errors.append(state['module'] + ' 冷初始化或续块标识与位置不符')
    previous.clear()
    previous.update(current)
    return errors


def disk_bytes(directory):
    return sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())


def describe(folder, ref, row, previous):
    frame = read(folder / ('end-' + row['forward_id'] + '.json'))
    cache = row['decode_cache']
    schedule = cache.get('schedule', {})
    position = row['positions'][0]
    errors = list(cache['errors']) + gdn_continuity(cache, previous, position)
    directory = Path(row['native_metadata'][0]['path']).parent
    if schedule and schedule.get('request_id') != row['client_request_id']:
        errors.append('调度所属请求与前向不同')
    observed = {layer['path'] for layer in cache.get('layers', []) if layer['invocations']}
    missing = [] if cache.get('format') == 'schedule_only' else [layer for layer in row['layers'] if layer not in observed]
    return {'missing_layers': missing, 'forward_id': row['forward_id'], 'request_id': row['client_request_id'], 'internal_id': row['internal_id'],
            'schedule_id': cache.get('schedule_id'), 'phase': schedule.get('phase'), 'step': row['step'],
            'rank': row['rank'], 'input_ids': row['input_ids'], 'position_range': [position, row['positions'][-1]+1],
            'output_index': row['output_index'], 'token_id': row.get('token_id'), 'row': ref,
            'msprobe_bytes': disk_bytes(directory), 'msprobe_window_seconds': frame['probe_window_seconds'],
            'snapshot_bytes': disk_bytes(folder / ('cache-' + row['forward_id'])),
            'observer_seconds': cache.get('observer_seconds', 0.0),
            'coverage': row['coverage']['status'], 'cache_status': 'incomplete' if errors else cache['status'],
            'errors': errors}


def verify_progress(row, item, mapped, sequence, cursor, accepted, tokens):
    start, stop = item['position_range']
    if (start != cursor or row['positions'] != list(range(start, stop)) or row['input_ids'] != sequence[start:stop]
            or row['internal_id'] != mapped['internal_id']):
        raise ValueError('冷前缀时间线输入、位置或请求身份不连续')
    prompt_length = len(mapped['prompt_ids'])
    phase = 'prefill' if start < prompt_length else 'decode'
    item['phase_source'] = 'scheduler'
    if item['phase'] != phase:
        item['errors'].append('缺少匹配的调度阶段')
        item.update(scheduled_phase=item['phase'], phase=phase, phase_source='worker_input_positions')
    if stop < prompt_length:
        if row['output_index'] is not None:
            raise ValueError('未完成的 prefill 块意外关联输出')
        return stop, accepted
    if row['output_index'] != accepted or row.get('token_id') != tokens[accepted]:
        raise ValueError('前向与已接受输出序号不匹配')
    return stop, accepted + 1


def initial_position(config, entries):
    if config.get('prefix_reuse') and entries:
        return entries[0][0]['positions'][0]
    return 0


def seal_timeline(folder, mapped, tokens, rows, extra, intent):
    entries = sorted(((read_bound(ref), ref) for ref in [*rows, *extra]), key=lambda item: item[0]['step'])
    steps = {row['step'] for row, _ in entries}
    if len(steps) != len(entries):
        raise ValueError('同一请求的 msProbe step 重复')
    prompt = mapped['prompt_ids']
    sequence = prompt + tokens[:-1]
    timeline, previous, errors = [], {}, []
    config = intent['cache_capture']
    cursor = initial_position(config, entries)
    accepted = 0
    for row, ref in entries:
        item = describe(folder, ref, row, previous)
        cursor, accepted = verify_progress(row, item, mapped, sequence, cursor, accepted, tokens)
        if item['errors']:
            item['cache_status'] = 'incomplete'
            errors.extend(f"{row['forward_id']}: {reason}" for reason in item['errors'])
        timeline.append(item)
    if cursor != len(sequence) or accepted != len(tokens):
        raise ValueError('时间线未覆盖实际输入或输出')
    totals = {k: sum(item[k] for item in timeline) for k in
              ('msprobe_bytes', 'msprobe_window_seconds', 'snapshot_bytes', 'observer_seconds')}
    if totals['snapshot_bytes'] > intent['cache_capture']['max_request_snapshot_bytes']:
        errors.append('请求快照字节超过预算')
    prefix = {}
    if config.get('prefix_reuse'):
        from .prefix.receipt import reconcile
        prefix['prefix_reuse'] = reconcile(folder, timeline, prompt, config)
        errors.extend(prefix['prefix_reuse']['errors'])
    return {**prefix, 'timeline': timeline, 'cache_capture': intent['cache_capture'], 'capture_totals': totals, 'cache_status': 'incomplete' if errors else 'complete',
            'cache_errors': errors}


def select_entry(items, request):
    if 'forward_id' in request:
        matches = [item for item in items if item['forward_id'] == request['forward_id']]
    else:
        position = request['prompt_position']
        matches = [item for item in items if item['phase'] == 'prefill'
                   and item['position_range'][0] <= position < item['position_range'][1]]
    if len(matches) != 1:
        raise ValueError('请求中没有唯一匹配的前向或 prompt 位置')
    return matches[0]
