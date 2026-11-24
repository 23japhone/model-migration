"""记录图内设备复制和图外执行处的快照，跨步复用前同步导出。"""
from contextlib import ExitStack
from functools import partial
from inspect import signature
from time import perf_counter

from .. import state
from ..decode_cache.snapshots import Snapshots, SnapshotBudget
from ..decode_cache.runtime import validate_backend
from .replay import active, capturing

_library = None


def _tap(value, layer, name):
    from .hooks import recorder
    if recorder is not None and recorder.observer is not None:
        recorder.observer.record(value, layer, name)


def register():
    global _library
    if _library is None:
        import torch
        from torch.fx.node import has_side_effect
        _library = torch.library.Library('migration_graph', 'DEF')
        _library.define('tap(Tensor value, int layer, str name) -> ()')
        _library.impl('tap', _tap, 'PrivateUse1')
        _library.impl('tap', lambda value, layer, name: None, 'Meta')
        has_side_effect(torch.ops.migration_graph.tap.default)


def edge(layer, side, position, module, args, kwargs, result=None):
    import torch
    value = kwargs.get('hidden_states') if side == 'input' else result
    if value is None and side == 'input' and len(args) > position:
        value = args[position]
    if isinstance(value, (tuple, list)):
        value = value[0]
    if isinstance(value, torch.Tensor):
        torch.ops.migration_graph.tap(value, layer, side)


class GraphObservations:
    def __init__(self, model, architecture, config):
        from .states import install_state
        self.limit = config['max_snapshot_bytes']
        self.device_limit = config.get('max_device_snapshot_bytes', self.limit)
        self.allocated = 0
        self.folder = None
        self.scopes = ExitStack()
        self.updated_types = set()
        self.cache_layers = {}
        self.block_sizes = {}
        modules = dict(model.named_modules())
        register()
        for layer in architecture['layers']:
            idx = layer['index']
            block = modules[layer['path']]
            position = list(signature(block.forward).parameters).index('hidden_states')
            block.register_forward_pre_hook(partial(edge, idx, 'input', position), with_kwargs=True)
            block.register_forward_hook(partial(edge, idx, 'output', position), with_kwargs=True)
            for target in layer['states']:
                module = modules[target['module']]
                validate_backend(module, target['kind'])
                install_state(self.scopes, module, idx, target, self)

    def start(self, folder, frame):
        if folder != self.folder:
            self.remaining = state.read(folder / 'intent.json')['cache_capture']['max_request_snapshot_bytes']
        self.folder, self.frame = folder, frame
        self.direct = {'buffers': {}, 'errors': [], 'read_sources': {}}
        self.pending = []
        self.used = set()
        self.read_calls = []

    def binding(self):
        return capturing.get() or (self.direct if active.get() else None)

    def record(self, value, layer, name):
        binding = self.binding()
        if binding is None:
            return
        key = (layer, name)
        size = value.numel() * value.element_size()
        if key in binding['buffers']:
            binding['errors'].append(f'{layer}/{name} 观察点重复')
        elif size > self.device_limit - self.allocated:
            binding['errors'].append(f'{layer}/{name} 设备快照预算不足')
        else:
            binding['buffers'][key] = value.detach().clone()
            self.allocated += size

    def replayed(self, item):
        key = item['graph_id']
        if key in self.used:
            self.pending = [(source, binding) for source, binding in self.pending if source['graph_id'] != key]
            self.frame['errors'].append(f'{key} 缓冲区在本次导出前已覆盖')
            return
        self.used.add(key)
        self.pending.append(({'kind': 'graph', 'graph_id': key, 'ordinal': item['ordinal']}, item['binding']))

    def finish(self):
        import torch
        from mindie_debugger.precision import MemoryWatchpoint
        start = perf_counter()
        torch.npu.synchronize()
        from .read_plan import materialize
        try:
            self.frame['backend_reads'] = materialize(self, self.frame)
        except (KeyError, ValueError, IndexError, RuntimeError, StopIteration) as error:
            self.frame['errors'].append('注意力实参 '+str(error))
        self.pending.append(({'kind': 'outside_graph', 'forward_id': self.frame['forward_id']}, self.direct))
        budget = SnapshotBudget(min(self.limit, self.remaining))
        initial = budget.remaining
        folder = self.folder / ('snapshots-' + self.frame['forward_id'])
        folder.mkdir()
        layers = self.frame['observations'] = {}
        for source, binding in self.pending:
            self.frame['errors'].extend(binding['errors'])
            for (layer, name), value in binding['buffers'].items():
                row = layers.setdefault(str(layer), {'files': {}, 'sources': {}})
                target = folder / str(layer)
                target.mkdir(exist_ok=True)
                try:
                    tensor = valid_view(value, name, self.frame['count'])
                    store = Snapshots(target, budget, MemoryWatchpoint)
                    store.boundary(name, tensor)
                    row['files'][name] = store.files[name]
                    row['sources'][name] = source
                except (RuntimeError, ValueError, OSError) as error:
                    self.frame['errors'].append(f'{layer}/{name}: {error}')
            for layer, source_name in binding['read_sources'].items():
                layers.setdefault(str(layer), {'files': {}, 'sources': {}})['read_source'] = source_name
        self.remaining -= initial - budget.remaining
        self.allocated -= sum(t.numel()*t.element_size() for t in self.direct['buffers'].values())
        self.frame.update(snapshot_bytes=initial-budget.remaining, export_seconds=perf_counter()-start,
                          device_snapshot_bytes=self.allocated)
        self.pending.clear()
        self.direct['buffers'].clear()


def valid_view(tensor, name, count):
    if name in ('input', 'output', 'key.input', 'value.input', 'key.after', 'value.after'):
        if tensor.shape[0] < count:
            raise ValueError('采集张量有效 Token 数不足')
        return tensor[:count]
    return tensor
