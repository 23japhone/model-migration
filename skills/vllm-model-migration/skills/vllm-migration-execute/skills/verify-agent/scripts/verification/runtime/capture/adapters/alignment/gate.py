"""在 msProbe 原生前/后回调内只取计划列出的实际参数和输出。"""
from collections import Counter
from contextlib import ExitStack, contextmanager
from functools import partial
import hashlib
import os
import re
from threading import get_ident
from tempfile import NamedTemporaryFile

from .arguments import verify_arguments
from .plan import capture_slice


def module_path(name):
    match = re.fullmatch(r'Module\.(.+)\.[^.]+\.forward\.\d+', name) if isinstance(name, str) else None
    return match.group(1) if match else None


def physical_key(call, hardware):
    owner, name = call[hardware]['owner'], call[hardware]['name']
    if call['kind'] == 'module':
        path = name if not owner or name == owner or name.startswith(owner + '.') else owner + '.' + name
        return 'module', path
    return 'api', owner, name


def operator_key(name, parent):
    module = module_path(name)
    if module is not None:
        return 'module', module
    match = re.fullmatch(r'(.+)\.\d+\.forward', name)
    if match:
        return 'api', module_path(parent) or '', match.group(1)
    return None


def replace(stack, instance, name, value):
    previous = vars(instance).get(name)
    if name in vars(instance):
        stack.callback(setattr, instance, name, previous)
    else:
        stack.callback(delattr, instance, name)
    setattr(instance, name, value)


class TargetGate:
    def __init__(self, plan, folder, hardware='npu'):
        self.folder = folder
        self.hardware = hardware
        self.plan = {(physical_key(call, hardware), call['occurrence']): call for call in plan['calls']}
        if len(self.plan) != len(plan['calls']):
            raise ValueError('同一实际调用对应多条计划')
        self.count = Counter()
        self.ordinal = 0
        self.pending = {}
        self.rows = []
        self.errors = []

    @contextmanager
    def intercept(self, collector):
        if not callable(getattr(collector, 'should_collect', None)):
            raise ValueError('当前 msProbe 缺少定向采集过滤接口')
        with ExitStack() as hooks:
            replace(hooks, collector, 'should_collect', lambda name, pid: False)
            for side in ('input', 'output'):
                method = partial(self.observe, side, collector.module_processor)
                replace(hooks, collector, f'forward_{side}_data_collect', method)
            yield

    def observe(self, side, processor, name, module, pid, module_input_output):
        if pid != os.getpid():
            return
        if side == 'input':
            parent = processor.api_parent_node.get(get_ident())
            key = operator_key(name, parent)
            index = self.count[key]
            self.count[key] += 1
            self.ordinal += 1
            call = self.plan.get((key, index))
            if call:
                self.begin(call, processor, name, parent, index, module_input_output)
        active = self.pending.get(name) if side == 'input' else self.pending.pop(name, None)
        if active:
            self.capture(side, active, module, module_input_output)

    def begin(self, call, processor, name, parent, index, io):
        try:
            attributes = verify_arguments(call, self.hardware, io)
        except ValueError as exc:
            self.errors.append({'call': call['id'], 'error': str(exc)})
            return
        if call['kind'] == 'module':
            parent = processor.module_node.get(name)
        self.pending[name] = call, {**attributes, 'ordinal': self.ordinal, 'native_name': name,
                                    'native_parent': module_path(parent) or '', 'occurrence': index}

    def capture(self, side, active, module, io):
        call, source = active
        for slot in call['slots']:
            path = slot[self.hardware]
            if (path[0] in ('output', 'post_input_args', 'post_input_kwargs')) != (side == 'output'):
                continue
            try:
                tensor = select_tensor(path, module, io)
                tensor, region = selected_value(tensor, capture_slice(slot, self.hardware))
                info = save(self.folder, tensor, len(self.rows))
                self.rows.append({**info, **source, **region, 'call': call['id'], 'slot': slot['id'], 'argument': path})
            except (AttributeError, IndexError, KeyError, OSError, OverflowError, RuntimeError, ValueError, TypeError) as exc:
                self.errors.append({'call': call['id'], 'slot': slot['id'], 'error': str(exc)})


def select_tensor(path, module, io):
    if path[0] == 'parameters':
        return module.get_parameter(path[1])
    if path[0] == 'output':
        value = io.output if isinstance(io.output, tuple) else (io.output,)
    else:
        value = io.args if path[0] in ('input_args', 'post_input_args') else io.kwargs
    for key in path[1:]:
        if not isinstance(value, (tuple, list, dict)):
            raise ValueError('目标参数树路径未命中张量')
        value = value[key]
    return value


def selected_value(value, selection):
    import torch
    scalar_dtype = {bool: torch.bool, int: torch.int64, float: torch.float64}
    if type(value) in scalar_dtype:
        value = torch.tensor(value, dtype=scalar_dtype[type(value)])
    if not isinstance(value, torch.Tensor):
        raise ValueError('指定的实参既不是 Tensor 也不是可保真的数值标量')
    if selection is None:
        return value, {}
    axis, start, stop = selection['axis'], selection['start'], selection['stop']
    if axis >= value.ndim or stop > value.shape[axis]:
        raise ValueError('声明的有效切片超出真实张量')
    return value.narrow(axis, start, stop - start), {'capture_slice': selection, 'original_shape': list(value.shape)}


def save(folder, tensor, index):
    import torch
    if not isinstance(tensor, torch.Tensor) or tensor.layout != torch.strided:
        raise ValueError('槽位不是 strided PyTorch Tensor')
    dest = folder / f'target-{index}.pt'
    # A contiguous view may still share its source's complete storage. Always copy only its logical elements.
    cpu = tensor.detach().to(device='cpu', copy=True, memory_format=torch.contiguous_format)
    with NamedTemporaryFile(dir=folder, suffix='.part', mode='w+b') as stream:
        torch.save(cpu, stream)
        stream.flush()
        stream.seek(0)
        digest = stream_sha(stream)
        os.link(stream.name, dest)
    return {'path': dest.name, 'sha256': digest,
            'shape': list(cpu.shape), 'dtype': str(cpu.dtype).removeprefix('torch.'), 'layout': 'strided'}


def stream_sha(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(2**20), b''):
        digest.update(block)
    return digest.hexdigest()
