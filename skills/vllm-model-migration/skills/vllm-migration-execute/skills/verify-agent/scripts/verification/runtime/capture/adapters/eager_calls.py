"""让已审核编译装饰器的直接 Eager forward 经过 msProbe 模块 hooks。"""
from functools import wraps
import hashlib
import importlib
from inspect import getsourcefile, unwrap
from pathlib import Path

from .sources import SOURCES

TARGET = 'vllm.compilation.decorators'
LOCAL_HOOKS = ('_forward_pre_hooks', '_forward_hooks', '_backward_pre_hooks', '_backward_hooks')
GLOBAL_HOOKS = tuple('_global' + name for name in LOCAL_HOOKS)


class EagerCalls:
    def __init__(self, model, recorder):
        self.modules = []
        self.paths = []
        for name, module in model.named_modules():
            original = type(module).__call__
            if original.__module__ != TARGET: continue
            digest = hashlib.sha256(Path(getsourcefile(unwrap(original))).read_bytes()).hexdigest()
            if digest not in SOURCES[TARGET]: raise ValueError('未适配的 Eager 装饰器源码')
            if getattr(module, 'do_not_compile', None) is not True: raise ValueError('要求直接 Eager 分支')
            self.modules.append(module); self.paths.append(name)
        self.validate()
        for module in self.modules: self.wrap(module, recorder)

    def validate(self):
        if not self.modules: return
        global_module = importlib.import_module('torch.nn.modules.module')
        if any(getattr(global_module, name) for name in GLOBAL_HOOKS):
            raise ValueError('已有全局 module hook，不能改变原直接调用的语义')
        for module in self.modules:
            if any(getattr(module, name) for name in LOCAL_HOOKS):
                raise ValueError('直接 Eager 模块已有用户 hook，不能改变其调用语义')

    @staticmethod
    def wrap(module, recorder):
        from torch.nn import Module
        cls = type(module); original = cls.__call__
        @wraps(original)
        def invoked(instance, *args, **kwargs):
            if instance is module and module_hooks_enabled(recorder.frame):
                if instance.do_not_compile is not True: raise ValueError('直接 Eager 模式已变化')
                return Module._call_impl(instance, *args, **kwargs)
            return original(instance, *args, **kwargs)
        cls.__call__ = invoked


def module_hooks_enabled(frame):
    if frame is None:
        return False
    if frame.get('profile') == 'tensor_alignment':
        return frame.get('targeted') is True
    return frame.get('profile') != 'token_prediction'
