"""冻结本步设备视图，导出线程只持有不可变 CPU 字节和本步目录。"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
import sys
from threading import BoundedSemaphore
from time import monotonic_ns

from .. import state
from ..decode_cache.snapshots import Snapshots, layout


class Exporter:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='capture-snapshot')
        self.space = BoundedSemaphore(4)

    def enqueue(self, path, data):
        self.space.acquire()
        try:
            return self.pool.submit(self.write, path, data)
        except BaseException:
            self.space.release()
            raise

    def write(self, path, data):
        try:
            with path.open('xb') as stream:
                stream.write(data)
        finally:
            self.space.release()


class Archive:
    def __init__(self, exporter):
        self.exporter = exporter
        self.writes = deque()

    def finish(self, folder, frame):
        for trace, name, result in self.writes:
            try:
                result.result()
            except Exception as error:
                frame['member_errors'].setdefault(trace, []).append(f'{name}: {error}')
        frame['archived_ns'] = monotonic_ns()
        state.write(folder / ('numeric-' + frame['forward_id'] + '.json'), frame)


class FrozenSnapshots(Snapshots):
    def __init__(self, folder, budget, watchpoint, archive, trace):
        super().__init__(folder, budget, watchpoint)
        self.archive = archive
        self.trace = trace

    def scope(self, name):
        path = self.folder / name
        path.mkdir(mode=0o700)
        return FrozenSnapshots(path, self.budget, self.watchpoint, self.archive, self.trace)

    def save(self, name, tensor):
        import torch
        if name in self.files or sys.byteorder != 'little':
            raise ValueError('异步快照名称重复或字节序不支持')
        size = self.reserve(tensor)
        self.budget.remaining -= size
        frozen = tensor.detach().clone(memory_format=torch.contiguous_format)
        if tensor.device.type == 'npu':
            event = torch.npu.Event()
            event.record(torch.npu.current_stream(tensor.device))
            event.synchronize()
        cpu = frozen.to('cpu')
        data = cpu.view(torch.uint8).numpy().tobytes()
        if len(data) != size:
            raise ValueError('异步快照字节数与原 dtype、shape 不符')
        filename = name + '.bin'
        path = self.folder / filename
        future = self.archive.exporter.enqueue(path, data)
        self.archive.writes.append((self.trace, str(path.relative_to(self.folder.parents[1])), future))
        preview = cpu.flatten()[:8].tolist()
        record = {**layout(tensor), 'file': filename, 'bytes': size, 'byte_order': 'little',
                  'sha256': hashlib.sha256(data).hexdigest(), 'preview': [v if math.isfinite(v) else str(v) for v in preview]}
        self.files[name] = record
        return record


def begin(rec, frame, rows):
    if not hasattr(rec, 'async_exporter'):
        rec.async_exporter = Exporter()
    archive = Archive(rec.async_exporter)
    rec.joint_archive = archive
    state.write(rec.folder / ('exporting-' + frame['forward_id'] + '.json'),
                {'members': [r['trace_id'] for r in rows]})


def publish(folder, frame, archive):
    try:
        archive.finish(folder, frame)
    except Exception as error:
        state.fail(folder, error)
    finally:
        (folder / ('exporting-' + frame['forward_id'] + '.json')).unlink(missing_ok=True)
