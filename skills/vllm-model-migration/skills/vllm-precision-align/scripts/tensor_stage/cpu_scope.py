"""从已确认的 PyTorch API 签名、完整操作数及标量实参推导等价 CPU 计算。"""
import math

# 每个位置参数可替换为 PyTorch 签名中的关键字。仅列出具有现行 CPU 实现的 API。
SIGNATURES = {
    'Torch.matmul': ('matmul', (('a','input'), ('b','other'))),
    'Torch.mul': ('mul', (('a','input'), ('b','other'))),
    'Torch.add': ('add', (('a','input'), ('b','other'))),
    'Functional.linear': ('linear', (('x','input'), ('weight','weight'), ('bias','bias'))),
    'Functional.silu': ('silu', (('x','input'),)),
    'Functional.softmax': ('softmax', (('x','input'),)),
}


def add_attributes(values):
    alpha = values.get('alpha', 1)
    if not values.keys() - {'alpha'} and type(alpha) in (int, float) and math.isfinite(alpha):
        return {'alpha': alpha}
    return None


def softmax_attributes(values):
    if not values.keys() - {'dim','dtype'} and type(values.get('dim')) is int and values.get('dtype') is None:
        return {'dim': values['dim']}
    return None


def scalar_attributes(op, boundary):
    # 空字典是“实际无标量实参”，缺字段则尚未核实。
    values = boundary.get('scalar_args')
    if values is None:
        return {} if op in ('mul','matmul','linear','silu') else None
    if not isinstance(values, dict):
        return None
    if op == 'add':
        return add_attributes(values)
    if op == 'softmax':
        return softmax_attributes(values)
    if not values or op == 'linear' and values == {'bias':None}:
        return {}
    if op == 'silu' and set(values) == {'inplace'} and type(values['inplace']) is bool:
        return {}
    return None


def signature_bindings(slots, side, op, arguments, boundary):
    available = {tuple(slot[side]): slot['id'] for slot in slots if slot['role'] != 'output'}
    bindings = {}
    for index, (argument, keyword) in enumerate(arguments):
        positional = available.pop(('input_args',index), None)
        named = available.pop(('input_kwargs',keyword), None)
        if positional is not None and named is not None:
            return None
        found = positional or named
        if found is not None:
            bindings[argument] = found
        elif not (op == 'linear' and argument == 'bias' and
                  (boundary.get('arguments_complete') is True or boundary.get('scalar_args') == {'bias':None})):
            return None
    if available:
        return None
    return bindings


def side_spec(call, side):
    boundary = call[side]
    signature = SIGNATURES.get(boundary['name'])
    if call['kind'] != 'api' or signature is None:
        return None
    op, arguments = signature
    attrs = scalar_attributes(op, boundary)
    if attrs is None:
        return None
    slots = call['slots']
    outputs = [slot['id'] for slot in slots if slot['role'] == 'output' and slot[side] == ['output',0]]
    if len(outputs) != 1 or any(slot.get('capture_slice') or slot.get('transforms') for slot in slots):
        return None
    inputs = signature_bindings(slots, side, op, arguments, boundary)
    if inputs is None:
        return None
    return {'op': op, 'inputs': inputs, 'output': outputs[0], 'attributes': attrs}


def derive(call):
    if 'cpu_reference' in call or call.get('unpaired_slots'):
        return call
    gpu = side_spec(call, 'gpu')
    if gpu is not None and gpu == side_spec(call, 'npu'):
        return {**call, 'cpu_reference':gpu}
    return call
