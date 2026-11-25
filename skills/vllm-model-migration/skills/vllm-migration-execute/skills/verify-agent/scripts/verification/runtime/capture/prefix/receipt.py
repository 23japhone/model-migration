"""将已采用的缓存块、生产前向和消费层的原始数值绑定。"""
from ....evidence import read_bound
from .compare import compare, ranges
from .sources import sources, spans


COMPONENTS = {'attention': ('key', 'value'), 'gdn': ('conv', 'ssm'), 'mla': ('mla.latent', 'mla.rope')}
FAILURES = (ValueError, KeyError, IndexError, OSError, TypeError)


def prefix_files(state, schedule):
    if not schedule.get('prefix_cache', {}).get('lookup', {}).get('length'):
        return set()
    names = COMPONENTS.get(state['kind'], ()) if state['kind'] != 'gdn' else ()
    return {name + '.prefix' for name in names}


def state_for(row, layer):
    record = row['decode_cache']['layers'][layer]
    call, = record['invocations']
    state, = call['states']
    return state


def link(source, consumer, layer, name, span, block):
    low, high, row = span
    producer = state_for(row, layer)
    event = producer['event']
    if producer['module'] != consumer['module'] or event['group_id'] != consumer['event']['group_id']:
        raise ValueError('生产和消费状态所属模块或缓存组不同')
    gdn = producer['kind'] == 'gdn'
    if not producer.get('msprobe_call'):
        raise ValueError('生产前向缺少与该层绑定的 msProbe 调用')
    route = consumer['event']['prefix_state'][name] if gdn else None
    if gdn:
        if (event[name]['row'] != block['block'] or route['source_row'] != block['block']
                or route['target_row'] != consumer['event'][name]['row']
                or not consumer['event'].get('has_initial_state', True)):
            raise ValueError('检查点来源行、实际更新行或初始化模式不同')
    else:
        validate_write(event, row, block, low, high)
    writer = producer['snapshots'][name + '.after']
    reader = consumer['snapshots'][name + ('.before' if gdn else '.prefix')]
    return {'request_id': row['client_request_id'], 'forward_id': row['forward_id'], 'row': source, 'source_receipt': row['_source_receipt'],
            'layer_index': layer, 'layer_path': row['architecture']['layers'][layer]['path'], 'layer': consumer['module'],
            'group_id': event['group_id'], 'component': name, 'range': [low, high],
            'residency': block, 'source_file': writer, 'consumer_file': reader, 'msprobe_call': producer['msprobe_call'],
            **({'state_copy': route} if gdn else {}),
            'byte_ranges': ranges(writer, reader, row, low, high, route)}


def validate_write(event, row, block, low, high):
    page = event['page_size']
    slots = event['write_slots']
    at = low - row['positions'][0]
    if slots[at:at+high-low] != [block['block']*page + pos%page for pos in range(low, high)]:
        raise ValueError('生产前向实际写槽与命中位置不同')


def collect_layer(report, index, row, layer, count, verified):
    consumer = state_for(row, layer['index'])
    event = consumer['event']
    gid, page = event['group_id'], event['page_size']
    if consumer['kind'] == 'attention' and not (event.get('read_plan_matches') and event.get('write_plan_matches')):
        raise ValueError('注意力实际读块或后缀写槽与计划不同')
    if consumer['kind'] == 'mla' and layer['status'] != 'complete':
        from ..adapters.decode_cache.backends.mla_evidence import verify_mla
        plan = row['decode_cache']['schedule']
        verify_mla(event, plan['groups'][gid], plan)
    blocks = report['lookup']['groups'][gid]
    for low, high, writer, block in spans(index, gid, blocks, page, count, consumer['kind'] == 'gdn'):
        for name in COMPONENTS[consumer['kind']]:
            try:
                part = link(writer['_source_row'], consumer, layer['index'], name, (low, high, writer), block)
                compare(part, verified)
                report['components'].append(part)
            except FAILURES as error:
                report['errors'].append(f"{writer['client_request_id']} {writer['forward_id']} 组 {gid} "
                                        f"层 {layer['index']} {name} [{low},{high}): {error}")


def collect(folder, timeline, prompt, config, row):
    entry = timeline[0]
    plan = row['decode_cache'].get('schedule', {})
    evidence = plan.get('prefix_cache', {})
    report = {'hit_tokens': None, 'first_computed_position': entry['position_range'][0], 'consumer_forward_id': row['forward_id'],
              'components': [], 'errors': []}
    lookup = evidence.get('lookup')
    if lookup is None or lookup['length'] != report['first_computed_position'] or plan.get('forward_id') != row['forward_id']:
        report['errors'].append('首个前向缺少匹配的本地前缀查找、采用块或执行身份')
        return report
    count = lookup['length']
    report.update(hit_tokens=count, lookup=lookup, epoch=evidence['epoch'])
    if not count or not config.get('observe_values', True):
        return report
    index = sources(folder, evidence['epoch'], prompt, report['errors'])
    verified = set()
    for layer in row['architecture']['layers']:
        if layer.get('kind') in ('stateless', 'mlp', 'feed_forward'):
            continue
        try:
            collect_layer(report, index, row, row['decode_cache']['layers'][layer['index']], count, verified)
        except FAILURES as error:
            report['errors'].append(f"层 {layer['index']} [0,{count}): {error}")
    return report


def reconcile(folder, timeline, prompt, config):
    from .coverage import coverage
    row = read_bound(timeline[0]['row'])
    report = collect(folder, timeline, prompt, config, row)
    fields = coverage(report, row['architecture']['layers'], COMPONENTS, config.get('observe_values', True))
    if fields['source_status'] == 'incomplete' and not report['errors']:
        report['errors'].append('前缀原值未覆盖全部有效位置和适用模型层')
    return {**report, **fields}
