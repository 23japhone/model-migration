"""同步观察算子边界的无拷贝视图，原 dtype 数值按前向限额落盘。"""
from contextlib import contextmanager
import hashlib
import math
import sys
from threading import get_ident
from contextvars import ContextVar
from time import perf_counter


_observation = ContextVar('snapshot_observation', default=None)


@contextmanager
def measure_observation(record):
    record['observer_seconds'] = 0.0
    token = _observation.set(record)
    try:
        yield
    finally:
        _observation.reset(token)


@contextmanager
def observer_only():
    from msprobe.core.dump.hook_manager import BaseHookManager
    switches = BaseHookManager.inner_switch
    tid = get_ident()
    previous = switches.get(tid, False)
    record = _observation.get()
    token = _observation.set(None)
    start = perf_counter()
    switches[tid] = True
    try:
        yield
    finally:
        switches[tid] = previous
        if record is not None:
            record['observer_seconds'] += perf_counter() - start
        _observation.reset(token)


def layout(tensor):
    return {'shape': list(tensor.shape), 'stride': list(tensor.stride()),
            'dtype': str(tensor.dtype), 'storage_offset': tensor.storage_offset(),
            'storage_ptr': tensor.untyped_storage().data_ptr(), 'device': str(tensor.device)}


def fence(tensor):
    if tensor.device.type == 'npu':
        import torch
        import torch_npu
        if torch_npu.get_npu_format(tensor) not in (0, 2):
            raise ValueError('缓存视图不是已适配的线性 NPU 格式')
        torch.npu.synchronize(tensor.device)


class SnapshotBudget:
    def __init__(self, size):
        self.remaining = size


class Snapshots:
    def __init__(self, folder, budget, watchpoint):
        self.folder = folder
        self.budget = budget if isinstance(budget, SnapshotBudget) else SnapshotBudget(budget)
        self.watchpoint = watchpoint
        self.files = {}

    def scope(self, name):
        folder = self.folder / name
        folder.mkdir(mode=0o700)
        return Snapshots(folder, self.budget, self.watchpoint)

    def boundary(self, name, tensor):
        self.reserve(tensor)
        fence(tensor)
        view = tensor.detach().contiguous()
        observed = self.watchpoint().watch(name, view).digest
        if self.save(name, view)['sha256'] != observed:
            raise ValueError('层边界快照与 mindie-debugger 观察值不一致')

    def reserve(self, tensor):
        size = tensor.numel() * tensor.element_size()
        if size <= 0 or size > self.budget.remaining:
            raise ValueError('目标快照为空或超过 max_snapshot_bytes')
        return size

    def save(self, name, tensor):
        import torch
        if name in self.files or sys.byteorder != 'little':
            raise ValueError('快照名称重复或字节序不支持')
        size = self.reserve(tensor)
        cpu = tensor.detach().to('cpu', copy=True).contiguous()
        data = cpu.view(torch.uint8).numpy().tobytes()
        if len(data) != size:
            raise ValueError('快照字节长度与张量不一致')
        filename = name + '.bin'
        with (self.folder / filename).open('xb') as stream:
            stream.write(data)
        values = cpu.flatten()[:8].tolist()
        record = {**layout(tensor), 'file': filename, 'byte_order': 'little', 'bytes': size, 'sha256': hashlib.sha256(data).hexdigest(),
                  'preview': [v if math.isfinite(v) else str(v) for v in values]}
        self.files[name] = record
        self.budget.remaining -= size
        return record

    def before(self, name, view, keep_value):
        self.reserve(view)
        if not view.is_contiguous():
            raise ValueError('目标状态行不是连续物理视图')
        fence(view)
        watch = self.watchpoint()
        digest = watch.watch(name, view).digest
        if keep_value and self.save(name + '.before', view)['sha256'] != digest:
            raise ValueError('更新前摘要与数值快照不一致')
        return watch, digest

    def after(self, name, view, handle, op):
        watch, before = handle
        fence(view)
        result = watch.check(op_name=op, active_streams=None)
        if result['rebound'] or any(x.get('rebound') for x in result['changes']):
            raise ValueError('目标缓存视图重新绑定')
        after = self.save(name + '.after', view)['sha256']
        observed = result['changes'][0]['digest_after'] if result['changed'] else before
        if after != observed:
            raise ValueError('更新后摘要与数值快照不一致')
        return {'digest_before': before, 'digest_after': after, 'changed': before != after}
