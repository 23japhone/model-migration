"""将 worker 的相对文件转为宿主机证据并按同一前向重验字节。"""
from math import prod
import re

from ...evidence import read_bound
from .adapters.state import read
from .adapters.parallel import request_folder
from .adapters.decode_cache.runtime import EXPECTED_OPS
from .tensors import file_ref

REQUIRED = {'key.input', 'value.input', 'key.after', 'value.after',
            'conv.before', 'conv.after', 'ssm.before', 'ssm.after'}
OPTIONAL = {'expected_' + key for key in REQUIRED if not key.endswith('.input')}
WIDTH = {'torch.bfloat16': 2, 'torch.float16': 2, 'torch.float32': 4, 'torch.float64': 8}


def validate_snapshot(request):
    if 'cache_snapshot' in request:
        name = request['cache_snapshot']
        if not isinstance(name, str) or name not in REQUIRED | OPTIONAL | {'input', 'output', 'ssm.initial'}:
            raise ValueError('cache_snapshot 须为已定义的缓存数值名称')


def check_ready(root, worker, preparation):
    for role, record in (('worker', worker), ('engine', read(root / 'engine.json'))):
        capability = record.get('decode_cache', {})
        if capability.get('status') != 'ready':
            raise ValueError(f'{role} 未就绪 decode_cache: ' + str(capability.get('reason', '未启用')))
    config = preparation.get('decode_cache')
    if not config or worker['decode_cache']['config_sha256'] != config['sha256']:
        raise ValueError('worker 缓存采集配置与准备回执不同')
    if read_bound(config)['status'] != 'available':
        raise ValueError('缓存探针准备未通过')


def check_identity(cache, frame):
    dispatch = frame.get('dispatch')
    if dispatch is not None and cache['schedule_id'] != dispatch['schedule_id']:
        raise ValueError('本地缓存计划与广播的调度身份不符')
    for key in ('forward_id', 'internal_id', 'output_index', 'positions', 'step', 'rank'):
        value = frame.get('scheduled_output_index', frame[key]) if key == 'output_index' else frame[key]
        if cache[key] != value:
            raise ValueError('缓存证据与输出前向不符 ' + key)


def check_files(directory, cache):
    if cache['status'] == 'complete' and not REQUIRED.issubset(cache['snapshots']):
        raise ValueError('缓存数值文件不完整')
    verify_files(directory, cache.get('snapshots', {}), REQUIRED | OPTIONAL)


def verify_files(directory, snapshots, allowed):
    for name, item in snapshots.items():
        if name not in allowed or item['file'] != name + '.bin':
            raise ValueError('未知的缓存快照文件')
        path = directory / item['file']
        reference = file_ref(path, directory)
        if reference['sha256'] != item['sha256'] or item.get('path', str(path)) != str(path):
            raise ValueError('缓存快照身份或摘要变化 ' + name)
        size = prod(item['shape']) * WIDTH[item['dtype']]
        if size != path.stat().st_size or size != item['bytes']:
            raise ValueError('缓存快照字节长度不符 ' + name)
        item.update(reference)


def check_schedule(folder, cache):
    plan = cache.get('schedule')
    if plan is None and cache['status'] != 'complete':
        return
    if cache['schedule_id'] != plan['schedule_id'] or not re.fullmatch('[0-9a-f]{32}', plan['schedule_id']):
        raise ValueError('缓存调度标识无效')
    folder = request_folder(folder)
    path = folder / ('schedule-' + plan['schedule_id'] + '.json')
    ref = file_ref(path, folder)
    if read(path) != plan or cache.get('schedule_ref', ref) != ref:
        raise ValueError('缓存调度快照已变化')
    if (plan['internal_id'], plan['output_index'], list(range(plan['position'], plan['position'] + plan.get('count', 1)))) != (
            cache['internal_id'], cache['output_index'], cache['positions']):
        raise ValueError('调度快照与前向不匹配')
    cache['schedule_ref'] = ref


def verify(folder, cache, row, *, sealing=False):
    check_identity(cache, row)
    check_schedule(folder, cache)
    directory = folder / ('cache-' + row['forward_id'])
    if cache.get('format') == 2:
        from .layer_evidence import verify_layers
        verify_layers(directory, cache, row, sealing=sealing)
        return
    check_files(directory, cache)
    if cache['status'] == 'complete':
        check_observations(cache)
    elif cache['status'] != 'incomplete' or not cache['errors']:
        raise ValueError('缓存证据缺少有效状态或失败原因')


def check_observations(cache):
    if cache['errors'] or cache['ops'] != EXPECTED_OPS:
        raise ValueError('缓存算子链不完整')
    snapshots = cache['snapshots']
    verify_digests(cache['attention'], snapshots, ('key', 'value'))
    verify_digests(cache['gdn'], snapshots, ('conv', 'ssm'))


def verify_digests(events, snapshots, names):
    for name in names:
        state = events[name]
        if state['digest_after'] != snapshots[name + '.after']['sha256']:
            raise ValueError('写后观测与数值文件不一致 ' + name)
        if state['changed'] != (state['digest_before'] != state['digest_after']):
            raise ValueError('缓存变化状态不一致 ' + name)
        if name in ('conv', 'ssm') and state['digest_before'] != snapshots[name + '.before']['sha256']:
            raise ValueError('写前观测与数值文件不一致 ' + name)
        if name in ('key', 'value') and state['matches_input'] != (state['digest_after'] == snapshots[name + '.input']['sha256']):
            raise ValueError('K/V 输入与写后值比较不一致 ' + name)


def link_calls(cache, calls):
    if cache['status'] != 'complete':
        return
    for kind in ('attention', 'gdn'):
        layer = cache[kind]
        matching = [call for call in calls if call['module'] == layer['module']]
        if len(matching) != 1:
            raise ValueError('msProbe 缺少唯一的本层调用 ' + layer['module'])
        layer.update(msprobe_call=matching[0]['call'], occurrence=matching[0]['occurrence'])


def from_frame(folder, frame, intent, ready, calls):
    if 'cache_capture' not in intent and intent.get('cache_output_index') != frame['output_index']:
        return {}
    row = {**frame, 'rank': ready['rank'], 'architecture': ready.get('architecture'), 'calls': calls}
    cache = frame.get('decode_cache')
    try:
        if not cache:
            raise ValueError('选定前向缺少缓存观测记录')
        end = read(folder / ('end-' + row['forward_id'] + '.json'))
        if end.get('decode_cache') != cache:
            raise ValueError('缓存记录与实际 forward 结束记录不同')
        if 'dispatch' in frame and cache.get('format') == 2:
            from .parallel.snapshots import isolate_files
            isolate_files(folder / ('cache-' + row['forward_id']), cache)
            if not calls:
                for layer in cache['layers']:
                    layer['status'] = 'incomplete'
                    layer['errors'].append('缺少独立 msProbe 层调用证据')
                cache['status'] = 'incomplete'
                cache['errors'].append('msProbe 原生索引不完整')
        verify(folder, cache, row, sealing=True)
        if cache.get('format') != 2:
            link_calls(cache, calls)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        cache = {k: row[k] for k in ('forward_id', 'internal_id', 'output_index', 'positions', 'step', 'rank')}
        cache.update(status='incomplete', errors=[str(error)])
    return {'decode_cache': cache}


def receipt_status(folder, intent, tokens):
    if 'cache_output_index' not in intent:
        return {}
    index = intent['cache_output_index']
    if index >= len(tokens):
        return {'cache_status': 'incomplete', 'cache_error': '实际输出未到达所选 decode'}
    cache = read(folder / f'output-{index}.json')['decode_cache']
    return {'cache_status': cache['status'], 'cache_errors': cache['errors']}
