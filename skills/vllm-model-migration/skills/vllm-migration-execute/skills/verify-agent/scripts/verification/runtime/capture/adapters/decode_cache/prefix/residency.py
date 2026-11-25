"""在真实缓存管理器查找和分配之间保留块身份；重新分配或清空使旧身份失效。"""
from functools import wraps
from uuid import uuid4

from ... import state


class Residency:
    def __init__(self, manager):
        self.epoch = uuid4().hex
        self.generations = {}
        self.target = self.lookup = None
        pool = manager.block_pool
        allocate, reset, lookup = pool.get_new_blocks, pool.reset_prefix_cache, manager.get_computed_blocks

        @wraps(allocate)
        def allocate_blocks(*args, **kwargs):
            blocks = allocate(*args, **kwargs)
            for block in blocks:
                self.generations[block.block_id] = self.generations.get(block.block_id, 0) + 1
            return blocks

        @wraps(reset)
        def reset_pool(*args, **kwargs):
            success = reset(*args, **kwargs)
            if success:
                self.epoch = uuid4().hex
                self.generations.clear()
            return success

        @wraps(lookup)
        def lookup_blocks(request):
            result = lookup(request)
            if request.request_id == self.target:
                try:
                    blocks, count = result
                    self.lookup = {'length': count, 'groups': self.groups(blocks)}
                except (AttributeError, TypeError, ValueError) as error:
                    self.lookup = {'error': str(error)}
            return result

        track_evictions(pool, self.generations)
        pool.get_new_blocks, pool.reset_prefix_cache = allocate_blocks, reset_pool
        manager.get_computed_blocks = lookup_blocks

    def block(self, block):
        if block.is_null:
            return None
        return {'block': block.block_id, 'generation': self.generations.get(block.block_id, 0),
                'epoch': self.epoch, 'hash': block.block_hash.hex() if block.block_hash else None}

    def groups(self, blocks):
        return [[self.block(b) for b in group] for group in blocks.blocks]


def track_evictions(pool, generations):
    evict = getattr(pool, '_maybe_evict_cached_block', None)
    if evict is None:
        return

    @wraps(evict)
    def evict_block(block):
        removed = evict(block)
        if removed:
            generations[block.block_id] = generations.get(block.block_id, 0) + 1
        return removed

    pool._maybe_evict_cached_block = evict_block


def prepare(cls):
    original = cls.schedule

    @wraps(original)
    def schedule(self, *args, **kwargs):
        folder = None
        try:
            folder, internal = state.mapped()
            if folder and state.read(folder / 'intent.json').get('cache_capture', {}).get('prefix_reuse'):
                if not hasattr(self, '_verify_prefix'):
                    self._verify_prefix = Residency(self.kv_cache_manager)
                self._verify_prefix.target = internal
                self._verify_prefix.lookup = None
        except Exception as error:
            if folder:
                try:
                    state.write(folder / 'prefix-error.json', {'error': str(error)})
                except OSError:
                    pass  # 调度证据缺失由 worker 和封存端记录。
        return original(self, *args, **kwargs)
    cls.schedule = schedule


def provenance(scheduler, output, internal, step):
    tracker = scheduler._verify_prefix
    allocation = tracker.groups(scheduler.kv_cache_manager.get_blocks(internal))
    evidence = {'epoch': tracker.epoch, 'allocation': allocation}
    if any(req.req_id == internal for req in output.scheduled_new_reqs):
        hit = tracker.lookup
        if hit is None or hit.get('length') != step['position']:
            raise ValueError('本地前缀查找与新请求已计算长度不一致')
        for actual, allocated in zip(hit['groups'], allocation, strict=True):
            for index, block in enumerate(actual):
                if block and (not block['hash'] or allocated[index] != block):
                    raise ValueError('实际采用的前缀块与查找结果不同')
        evidence['lookup'] = hit
    return evidence
