"""按结构清单、调用序号、调度分组及独立的 msProbe 调用核对全层回执。"""
from .adapters.decode_cache.layer_capture import layer_errors
from .adapters.decode_cache.prefill_attention import write_slots
from .adapters.decode_cache.plan import attention_plan, slot_ranges, state_row

from .adapters.decode_cache.backends import BY_KIND, MLA_OUTPUTS
from .adapters.decode_cache.backends.mla_evidence import read_ranges, verify_components, verify_mla

STATE_FILES = {kind: backend.files for kind, backend in BY_KIND.items()}
BOUNDARY = {'input', 'output'}


def verify_attention(event, group, position):
    size = event['kernel_block_size']
    expected = attention_plan(group['blocks'], group['page_size'], size, position)
    if any(event[k] != value for k, value in expected.items()):
        raise ValueError('注意力计划与调度不符')
    ranges = slot_ranges(event['read_blocks'], size, event['kv_length'])
    read_match = event['read_blocks'] == expected['expected_read_blocks'] and event['kv_length'] == position + 1
    if event['read_slot_ranges'] != ranges or event['read_plan_matches'] != read_match:
        raise ValueError('FIA 读范围与计划匹配结论不符')
    if event['write_plan_matches'] != (event['write_slot'] == expected['expected_slot']):
        raise ValueError('写槽匹配结论不符')


def verify_plan(state, schedule):
    event = state['event']
    group = schedule['groups'][event['group_id']]
    if state['address'] not in group['layers'] or event['layer'] != state['address']:
        raise ValueError('状态与调度分组不符')
    if event['blocks'] != group['blocks'] or event['page_size'] != group['page_size']:
        raise ValueError('分配块或页大小与调度不符')
    if event.get('observation') == 'prefill_kv':
        verify_prefill(event, group, schedule)
    elif state['kind'] == 'mla':
        verify_mla(event, group, schedule)
        ranges = read_ranges(event, schedule)
        if state.setdefault('read_ranges', ranges) != ranges:
            raise ValueError('MLA 读取区间与已核对的后端实参不同')
    elif state['kind'] == 'attention':
        verify_attention(event, group, schedule['position'])
    else:
        position = schedule['position'] + schedule.get('count', 1) - 1
        expected = state_row(group['blocks'], group['page_size'], position, event['mode'])
        if event['expected_row'] != expected:
            raise ValueError('状态行计划与调度不符')
        for name in ('conv', 'ssm'):
            part = event[name]
            if (part['plan_matches'], part['metadata_matches']) != (part['row'] == expected, part['row'] == event['metadata_row']):
                raise ValueError('状态行与元数据或计划匹配结论不符')


def verify_prefill(event, group, schedule):
    start, count, kernel = schedule['position'], schedule['count'], event['kernel_block_size']
    slots = write_slots(group, kernel, start, count)
    if event['expected_slots'] != slots or event['write_plan_matches'] != (event['write_slots'] == slots):
        raise ValueError('prefill 计划与实际写槽不符')
    end = start + count
    expected = attention_plan(group['blocks'], group['page_size'], kernel, end - 1)
    if event['causal_end_exclusive'] != list(range(start+1, end+1)) or event['kv_length'] != end:
        raise ValueError('prefill 读范围与因果约束不符')
    if event['read_source'] == 'paged_kv':
        if (event['read_plan_matches'] != (event['read_blocks'] == expected['expected_read_blocks'])
                or event['read_slot_ranges'] != slot_ranges(event['read_blocks'], kernel, end)):
            raise ValueError('prefill 读取的物理块与调度计划不同')
    elif event['read_source'] != 'input_kv' or start != 0:
        raise ValueError('prefill 输入来源无效')


def required_snapshots(state):
    required = STATE_FILES.get(state['kind'], set())
    if state['kind'] == 'mla' and state.get('event', {}).get('backend_path') == 'prefill':
        return required | MLA_OUTPUTS
    if state.get('event', {}).get('observation') == 'prefill_gdn':
        return required | {'ssm.initial'}
    return required


def describe_missing(call):
    call['missing_boundary'] = sorted(BOUNDARY.difference(call['boundary']))
    for state in call['states']:
        state['missing_snapshots'] = sorted(required_snapshots(state).difference(state['snapshots']))


def verify_state(state, descriptor, directory, schedule, complete):
    from .cache_evidence import verify_files, verify_digests
    if any(state[key] != value for key, value in descriptor.items()):
        raise ValueError('状态身份与模型结构不符')
    files = state['snapshots']
    required = required_snapshots(state)
    optional = {'expected_' + key for key in required if not key.endswith('.input')}
    verify_files(directory, files, required | optional)
    if complete:
        if not required or not required.issubset(files):
            raise ValueError('层状态张量文件缺失')
        verify_plan(state, schedule)
        if state['kind'] == 'mla':
            verify_components(state['event'], files, schedule.get('count', 1))
            return
        names = ('key', 'value') if state['kind'] == 'attention' else ('conv', 'ssm')
        if state['event'].get('observation') == 'prefill_kv':
            for name in names:
                part = state['event'][name]
                if (part['matches_input'] != (files[name + '.input']['sha256'] == files[name + '.after']['sha256'])
                        or len(part['matches_by_position']) != schedule['count'] or all(part['matches_by_position']) != part['matches_input']):
                    raise ValueError('prefill K/V 逐位置写后值不同')
        else:
            verify_digests(state['event'], files, names)


def correlate_call(call, matches, ordinal, complete):
    if ordinal >= len(matches) and not complete:
        return
    source = matches[ordinal]
    if source['occurrence'] != ordinal or call.get('msprobe_call', source['call']) != source['call']:
        raise ValueError('msProbe 层调用序号或标识不符')
    call['msprobe_call'] = source['call']


def verify_layer(layer, descriptor, directory, row):
    from .cache_evidence import verify_files
    schedule = row['decode_cache']['schedule']
    complete = layer['status'] == 'complete'
    if complete == bool(layer_errors(layer)):
        raise ValueError('层采集状态与实际错误不符')
    matches = [c for c in row['calls'] if c['module'] == layer['path']]
    if complete and len(matches) != len(layer['invocations']):
        raise ValueError('层调用次数与 msProbe 不符')
    for ordinal, call in enumerate(layer['invocations']):
        if not complete and 'phase' in schedule:
            describe_missing(call)
        folder = directory / f'call-{ordinal}'
        if call['occurrence'] != ordinal:
            raise ValueError('层调用序号错误')
        correlate_call(call, matches, ordinal, complete)
        verify_files(folder, call['boundary'], BOUNDARY)
        states = descriptor['states']
        if len(call['states']) != len(states) and complete:
            raise ValueError('缺少层状态观察点')
        for i, state in enumerate(call['states']):
            verify_state(state, states[i], folder / f'state-{i}', schedule, complete)
            matched = [c for c in row['calls'] if c['module'] == state.get('msprobe_module', state['module'])]
            correlate_call(state, matched, ordinal, complete)


def plan_disagreements(layer):
    for invocation in layer['invocations']:
        for state in invocation['states']:
            event = state['event']
            if state['kind'] == 'mla':
                continue  # verify_mla 和 verify_components 已逐分量核对。
            if state['kind'] == 'attention':
                ok = (event['read_plan_matches'] and event['write_plan_matches']
                      and event['key']['matches_input'] and event['value']['matches_input'])
            else:
                ok = all(event[key]['plan_matches'] and event[key]['metadata_matches'] for key in ('conv', 'ssm'))
            if not ok:
                yield state['module'] + ' 实际读写与计划或待写数值不同'


def recover_mla_files(directory, cache, layer, descriptor, row):
    from .parallel.snapshots import isolate_files
    if not any(s['kind'] == 'mla' for s in descriptor['states']):
        return False
    problems = isolate_files(directory, {'layers': [layer], 'errors': []})
    if not problems:
        return False
    cache['errors'].extend(problems)
    cache['status'] = 'incomplete'
    verify_layer(layer, descriptor, directory / f"layer-{layer['index']}", row)
    return True


def verify_layers(directory, cache, row, *, sealing=False):
    expected = row['architecture']['layers']
    layers = cache['layers']
    if len(layers) != len(expected):
        raise ValueError('层回执数量与 worker 结构清单不同')
    for index, (layer, desc) in enumerate(zip(layers, expected)):
        if (layer['index'], layer['path'], layer['kind']) != (index, desc['path'], desc['kind']):
            raise ValueError('层回执与结构清单不匹配')
        try:
            verify_layer(layer, desc, directory / f'layer-{index}', row)
            if sealing and 'phase' in cache.get('schedule', {}) and layer['status'] == 'complete':
                differences = list(plan_disagreements(layer))
                if differences:
                    layer['errors'].extend(differences)
                    layer['status'] = cache['status'] = 'incomplete'
                    cache['errors'].extend(differences)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            if recover_mla_files(directory, cache, layer, desc, row):
                continue
            if not sealing:
                raise
            reason = f"{layer['path']} 层证据无效: {error}"
            layer.update(invocations=[], status='incomplete', errors=[reason])
            cache['status'] = 'incomplete'
            cache['errors'].append(reason)
    complete = all(layer['status'] == 'complete' for layer in layers) and not cache['errors']
    if (cache['status'] == 'complete') != complete:
        raise ValueError('全层覆盖结论与各层证据不符')


def select_snapshot(cache, name, layer, occurrence):
    items = []
    for block in cache['layers']:
        calls = block['invocations']
        if occurrence >= len(calls):
            continue
        call = calls[occurrence]
        if layer in (None, block['path']) and name in call['boundary']:
            items.append(call['boundary'][name])
        for state in call['states']:
            if layer in (None, block['path'], state['module']) and name in state['snapshots']:
                items.append(state['snapshots'][name])
    if len(items) != 1:
        issues = [msg for block in cache['layers'] if layer in (None, block['path']) for msg in block['errors'] if name in msg]
        raise ValueError(f'缓存快照 {name} 匹配 {len(items)} 项，请指定唯一层与调用序号 ' + ' '.join(issues))
    return items[0]
