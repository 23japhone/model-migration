"""构造有来源、实际执行区间、非连续物理页和原始字节的回执。"""
import hashlib
import json


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, value):
    path.write_text(json.dumps(value))
    return ref(path)


def snapshot(folder, label, values):
    path = folder / (label + '.bin')
    path.write_bytes(bytes(values))
    return {**ref(path), 'bytes': len(values), 'dtype': 'torch.bfloat16', 'shape': [len(values)//2, 1]}


def block(index):
    return {'epoch': 'service', 'block': index, 'generation': 1, 'hash': str(index)}


KINDS = [('attention', ('key', 'value')), ('gdn', ('conv', 'ssm')), ('mla', ('mla.latent', 'mla.rope'))]


def layer(folder, fid, gid, kind, components, consumer, ids, start, end):
    event = {'group_id': gid, 'page_size': 2, 'read_plan_matches': True, 'write_plan_matches': True, 'has_initial_state': consumer}
    if kind != 'gdn':
        event['write_slots'] = [ids[gid][p//2]['block']*2+p%2 for p in range(start,end)] if not consumer else [100]
    files = {}
    for i, name in enumerate(components):
        data = [gid*11+i, 7]*2 if kind == 'gdn' else [p+gid+i for p in (range(4) if consumer else range(start,end)) for _ in range(2)]
        suffix = '.after'
        if consumer:
            suffix = '.before' if kind == 'gdn' else '.prefix'
        files[name+suffix] = snapshot(folder, fid+name, data)
        if kind == 'gdn':
            event[name] = {'row': 11 if consumer else 9}
            event.setdefault('prefix_state', {})[name] = {'source_row': 9, 'target_row': 11, 'copy_ranges': [[0,0,4]]}
    state = {'kind': kind, 'module': kind+'.module', 'msprobe_call':kind+'.0', 'event': event, 'snapshots': files}
    return {'index': gid, 'path': kind, 'status': 'complete', 'invocations': [{'states':[state]}]}


def entry(folder, consumer, start, end):
    fid = ('B' if consumer else 'A') + str(start)
    ids = [[block(5), block(8)], [None, block(9)], [block(6), block(12)]]
    allocation = {'epoch': 'service', 'allocation': ids}
    if consumer:
        allocation['lookup'] = {'groups': ids, 'length': 4}
    groups = [{'page_size': 2}]*3
    layers = []
    for gid, (kind, components) in enumerate(KINDS):
        layers.append(layer(folder, fid, gid, kind, components, consumer, ids, start, end))
    cache = {'layers': layers, 'schedule': {'prefix_cache': allocation, 'groups': groups, 'forward_id': fid}}
    row = {'decode_cache': cache, 'forward_id':fid, 'client_request_id':fid[0], 'positions':list(range(start,end)),
           'architecture': {'layers':[{'index':i,'path':kind,'kind':kind} for i,(kind,_) in enumerate(KINDS)]}}
    return {'forward_id':fid,'phase':'prefill','position_range':[start,end],'row':save(folder/(fid+'.json'),row)}


def pair(root):
    producer = root/'A'
    consumer = root/'B'
    producer.mkdir()
    consumer.mkdir()
    a = [entry(producer, False, 0, 2), entry(producer,False,2,4)]
    b = entry(consumer,True,4,5)
    prompt = [22,22,13,13,44]
    rec = save(producer/'receipt.json', {'request_id':'A', 'cache_capture':{'prefix_reuse':True}, 'timeline':a,
               'engine_input':save(producer/'request.json',{'prompt_ids':prompt[:-1]+[77]})})
    save(root/'lease.json',{'sealed':[{'trace_id':'A','receipt':rec}]})
    return consumer, [b], prompt
