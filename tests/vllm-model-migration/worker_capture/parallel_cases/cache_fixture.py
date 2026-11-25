"""合成可区分 rank 和前向的缓存原值，无需设备或张量 SDK。"""
import struct

from worker_capture.session_fixture import reference, write

LAYER = 'model.layers.0'
STATE = {'address': LAYER, 'module': LAYER, 'kind': 'attention'}
ARCH = {'layers': [{'path': LAYER, 'kind': 'attention', 'states': [STATE]}]}


def snapshot(directory, name, values):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + '.bin')
    path.write_bytes(struct.pack(f'<{len(values)}f', *values))
    return {'file': path.name, 'sha256': reference(path)['sha256'], 'bytes': 4 * len(values),
            'shape': [len(values)], 'dtype': 'torch.float32'}


def cache_witness(directory, frame):
    group = {'blocks': [1], 'page_size': 16, 'layers': [LAYER]}
    plan = {**frame['dispatch'], 'groups': [group]}
    start = plan['position']
    count = plan['count']
    write(directory.parent / ('schedule-' + plan['schedule_id'] + '.json'), plan)
    folder = directory / ('cache-' + frame['forward_id']) / 'layer-0' / 'call-0'
    values = [float(frame['rank'] * 100 + p) for p in frame['positions']]
    boundary = {name: snapshot(folder, name, values) for name in ('input', 'output')}
    files = {name: snapshot(folder / 'state-0', name, values) for name in ('key.input', 'key.after', 'value.input', 'value.after')}
    event = {'group_id': 0, 'layer': LAYER, 'blocks': [1], 'page_size': 16, 'kernel_block_size': 16,
             'read_blocks': [1], 'read_slot_ranges': [[16, 16 + start + count]], 'read_plan_matches': True,
             'expected_read_blocks': [1], 'expected_read_slot_ranges': [[16, 16 + start + count]],
             'write_plan_matches': True, 'kv_length': start + count}
    if plan['phase'] == 'prefill':
        slots = list(range(16 + start, 16 + start + count))
        event.update(observation='prefill_kv', read_source='paged_kv' if start else 'input_kv',
                     causal_end_exclusive=list(range(start + 1, start + count + 1)), expected_slots=slots, write_slots=slots)
        part = {'matches_input': True, 'matches_by_position': [True] * count}
    else:
        event.update(expected_slot=16 + start, write_slot=16 + start)
        part = {'matches_input': True, 'digest_after': files['key.after']['sha256'], 'digest_before': '', 'changed': True}
    event.update(key=part, value=part)
    state = {**STATE, 'ops': ['reshape_and_cache', 'fia'], 'errors': [], 'event': event, 'snapshots': files}
    call = {'errors': [], 'occurrence': 0, 'boundary': boundary, 'states': [state]}
    layer = {'kind': 'attention', 'index': 0, 'path': LAYER, 'status': 'complete', 'errors': [], 'invocations': [call]}
    identity = {k: frame[k] for k in ('schedule_id', 'forward_id', 'internal_id', 'rank', 'step', 'positions', 'output_index')}
    return {**identity, 'schedule': plan, 'format': 2, 'layers': [layer], 'status': 'complete', 'errors': []}
