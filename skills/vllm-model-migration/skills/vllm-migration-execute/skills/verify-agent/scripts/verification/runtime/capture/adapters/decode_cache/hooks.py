"""只在目标实例的已审核 Python 方法内观察原调用，离开 forward 即恢复。"""
from functools import wraps
from types import FunctionType, MethodType

from .snapshots import observer_only


class Names:
    """代理指定的模块属性，其余属性交还原对象。"""
    def __init__(self, original, **override):
        self.original = original
        self.override = override

    def __getattr__(self, name):
        if name in self.override:
            return self.override[name]
        return getattr(self.original, name)


def bind_method(stack, owner, name, reference, replacement):
    bound = getattr(owner, name)
    function = bound.__func__
    namespace = {**function.__globals__, reference: replacement}
    local = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
    local.__kwdefaults__ = function.__kwdefaults__
    if name in vars(owner):
        stack.callback(setattr, owner, name, vars(owner)[name])
    else:
        stack.callback(delattr, owner, name)
    setattr(owner, name, MethodType(local, owner))


def around(original, label, seen, errors, before, after=None):
    @wraps(original)
    def observed(*args, **kwargs):
        seen.append(label)
        ready = False
        try:
            with observer_only():
                before(kwargs)
                ready = True
        except Exception as error:
            errors.append(f'{label} before: {error}')
        result = original(*args, **kwargs)
        if ready and after:
            try:
                with observer_only():
                    after()
            except Exception as error:
                errors.append(f'{label} after: {error}')
        return result
    return observed


def bind_attention(stack, impl, capture, seen, errors):
    device = impl.reshape_and_cache.__func__.__globals__['DeviceOperator']
    write = around(device.reshape_and_cache, 'reshape_and_cache', seen, errors, capture.before_write, capture.after_write)
    bind_method(stack, impl, 'reshape_and_cache', 'DeviceOperator', Names(device, reshape_and_cache=write))
    npu = impl.forward_fused_infer_attention.__func__.__globals__['torch_npu']
    read = around(npu.npu_fused_infer_attention_score, 'fia', seen, errors, capture.read)
    bind_method(stack, impl, 'forward_fused_infer_attention', 'torch_npu', Names(npu, npu_fused_infer_attention_score=read))


def bind_gdn(stack, layer, capture, seen, errors):
    torch = layer._forward_core.__func__.__globals__['torch']
    ops = torch.ops._C_ascend
    wrappers = {}
    for name, op in [('conv', 'npu_causal_conv1d_custom'), ('ssm', 'npu_recurrent_gated_delta_rule')]:
        wrappers[op] = around(getattr(ops, op), op, seen, errors,
                              lambda args, name=name: capture.before(name, args),
                              lambda name=name: capture.after(name))
    local = Names(torch, ops=Names(torch.ops, _C_ascend=Names(ops, **wrappers)))
    bind_method(stack, layer, '_forward_core', 'torch', local)
