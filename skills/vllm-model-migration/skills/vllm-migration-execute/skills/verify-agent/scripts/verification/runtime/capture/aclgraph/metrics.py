"""汇总目标请求实际执行的前向与所用捕图实例。"""


def totals(timeline):
    measurements = [entry['metrics'] for entry in timeline]
    result = {key: sum(m.get(key, 0) for m in measurements) for key in (
        'native_bytes', 'snapshot_bytes', 'forward_seconds', 'export_seconds')}
    result['peak_npu_allocated_bytes'] = max((m.get('peak_npu_allocated_bytes', 0) for m in measurements), default=0)
    result.update(replay_metrics(timeline))
    if measurements and 'started_monotonic' in measurements[0] and 'finished_monotonic' in measurements[-1]:
        result['worker_wall_seconds'] = measurements[-1]['finished_monotonic'] - measurements[0]['started_monotonic']
    return result


def replay_metrics(timeline):
    captures = {e['graph_id']: e['capture_seconds'] for entry in timeline for e in entry['replays'] if 'capture_seconds' in e}
    result = {'capture_seconds': sum(captures.values()), 'graph_instances': len(captures)}
    replays = [e for entry in timeline for e in entry['replays']]
    if replays and all('device_seconds' in e for e in replays):
        result['replay_device_seconds'] = sum(e['device_seconds'] for e in replays)
    return result
