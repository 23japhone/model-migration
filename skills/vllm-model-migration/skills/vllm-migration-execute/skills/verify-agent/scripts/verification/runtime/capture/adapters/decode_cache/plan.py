"""选定请求在调度器的完整分配，和标准单 rank 两类地址的推导。"""
from functools import wraps
from uuid import uuid4

from .. import state


def slot_ranges(blocks, block_size, length):
    return [[block * block_size, block * block_size + min(block_size, length - i * block_size)]
            for i, block in enumerate(blocks)]


def attention_plan(blocks, page, kernel, position):
    if kernel <= 0 or page < kernel or page % kernel or position < 0:
        raise ValueError('不支持的页大小或请求位置')
    expected = []
    for offset in range(0, position + 1, kernel):
        block = blocks[offset // page]
        if block < 0:
            raise ValueError('有效上下文中有空块')
        expected.append(block * (page // kernel) + (offset % page) // kernel)
    return {'expected_slot': expected[-1] * kernel + position % kernel,
            'expected_read_blocks': expected, 'expected_read_slot_ranges': slot_ranges(expected, kernel, position + 1)}


def state_row(blocks, page, position, mode):
    if mode not in ('none', 'all', 'align') or page <= 0 or position < 0:
        raise ValueError('不支持的 Mamba 状态行寻址模式')
    return blocks[position // page if mode == 'align' else 0]


def snapshot(scheduler, output, internal, index):
    cached = output.scheduled_cached_reqs
    if internal not in cached.req_ids:
        return None
    row = cached.req_ids.index(internal)
    if cached.num_output_tokens[row] != index or output.num_scheduled_tokens[internal] != 1:
        return None
    return allocation(scheduler, internal, index, cached.num_computed_tokens[row])


def allocation(scheduler, internal, index, position):
    blocks = scheduler.kv_cache_manager.get_block_ids(internal)
    groups = []
    for group, ids in zip(scheduler.kv_cache_config.kv_cache_groups, blocks, strict=True):
        groups.append({'layers': list(group.layer_names), 'blocks': list(ids),
                       'page_size': group.kv_cache_spec.block_size})
    return {'schedule_id': uuid4().hex, 'internal_id': internal, 'output_index': index,
            'position': position, 'groups': groups}


def request_plan(scheduler, output, internal, folder):
    intent = state.read(folder / 'intent.json')
    if 'cache_capture' not in intent:
        index = intent.get('cache_output_index')
        return snapshot(scheduler, output, internal, index) if index is not None else None
    step = step_position(output, internal, folder)
    return {**allocation(scheduler, internal, step['output_index'], step['position']), **step,
            'request_id': intent['request_id']}


def step_position(output, internal, folder):
    prompt_size = len(state.read(folder / 'request.json')['prompt_ids'])
    fresh = [r for r in output.scheduled_new_reqs if r.req_id == internal]
    if fresh:
        position, index = fresh[0].num_computed_tokens, 0
        if position != 0:
            raise ValueError('full_request 要求冷前缀，新请求已有缓存位置')
    else:
        cached = output.scheduled_cached_reqs
        row = cached.req_ids.index(internal)
        position, index = cached.num_computed_tokens[row], cached.num_output_tokens[row]
    count = output.num_scheduled_tokens[internal]
    phase = 'prefill' if position < prompt_size else 'decode'
    if phase == 'prefill' and position + count > prompt_size:
        raise ValueError('调度区间跨越 prompt 与 decode')
    return {'phase': phase, 'count': count, 'position': position, 'output_index': index,
            'prompt_length': prompt_size}


def prepare_scheduler(cls):
    path = state.root() / 'decode-cache.json'
    parallel = (state.root() / 'parallel.json').is_file()
    if parallel:
        install_schedule(cls, parallel=True)
    if not path.is_file():
        return {}
    try:
        config = state.read(path)
        if config['status'] != 'available':
            raise ValueError(config.get('reason', '缓存探针不可用'))
        if not parallel:
            install_schedule(cls)
        return {'decode_cache': {'status': 'ready'}}
    except Exception as error:
        return {'decode_cache': {'status': 'unavailable', 'reason': str(error)}}


def install_schedule(cls, parallel=False):
    original = cls.schedule
    previous, ordinal = None, 0

    @wraps(original)
    def scheduled(self, *args, **kwargs):
        nonlocal previous, ordinal
        output = original(self, *args, **kwargs)
        folder = None
        try:
            folder, internal = state.mapped()
            if internal not in output.num_scheduled_tokens:
                return output
            if parallel:
                if previous != folder:
                    previous, ordinal = folder, 0
                step = step_position(output, internal, folder)
                identity = {'request_id': state.read(folder / 'intent.json')['request_id'], 'internal_id': internal,
                            'ordinal': ordinal, 'forward_id': uuid4().hex, 'schedule_id': uuid4().hex, **step}
                state.write(folder / f'dispatch-{ordinal}.json', identity)
                output._verify_forward = identity
                self._verify_dispatch = identity
                ordinal += 1
            plan = request_plan(self, output, internal, folder)
            if plan is not None:
                if parallel:
                    plan.update(identity)
                state.write(folder / ('schedule-' + plan['schedule_id'] + '.json'), plan)
                output._verify_cache_schedule_id = plan['schedule_id']
        except Exception as error:
            if folder is not None:
                try:
                    state.write(folder / 'cache-schedule-error.json', {'error': str(error)})
                except OSError:
                    pass  # worker 将以缺失调度 ID 封存这次观测。
        return output
    cls.schedule = scheduled
