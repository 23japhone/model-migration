"""具有已知数学定义的 CPU FP64 前向参考，仅使用经核验的调用输入。"""
from decimal import Decimal
import math

from .metrics import metrics
from . import dense_oracles


# (必需实参名、可选实参名、允许的算子属性)
SIGNATURES = {
    'linear': ({'x', 'weight'}, {'bias'}, set()), 'matmul': ({'a', 'b'}, set(), set()),
    'add': ({'a', 'b'}, set(), {'alpha'}), 'mul': ({'a', 'b'}, set(), set()),
    'silu': ({'x'}, set(), set()), 'softmax': ({'x'}, set(), {'dim'}),
    'embedding': ({'indices', 'weight'}, set(), set()), 'rms_norm': ({'x', 'weight'}, set(), {'eps'}),
    'layer_norm': ({'x'}, {'weight','bias'}, {'normalized_shape','eps'}),
    'gelu': ({'x'}, set(), {'approximate'}),
    'scaled_dot_product_attention': ({'q','k','v'}, {'mask'}, dense_oracles.SDPA_ATTRIBUTES),
}


def evaluate(op, args, attrs, torch):
    if op in ('gelu', 'layer_norm'):
        return {'gelu': dense_oracles.gelu, 'layer_norm': dense_oracles.layer_norm}[op](args, attrs, torch)
    if op == 'linear':
        return torch.nn.functional.linear(args['x'], args['weight'], args.get('bias'))
    if op == 'silu':
        return torch.nn.functional.silu(args['x'])
    if op == 'softmax':
        if type(attrs.get('dim')) is not int:
            raise ValueError('softmax 须声明计算轴 dim')
        return torch.softmax(args['x'], dim=attrs['dim'])
    if op == 'rms_norm':
        eps = float(attrs['eps'])
        if not math.isfinite(eps) or eps < 0:
            raise ValueError('RMSNorm 需要实际的有限非负 eps')
        x = args['x']
        return x * torch.rsqrt(x.square().mean(dim=-1, keepdim=True) + eps) * args['weight']
    if op == 'embedding':
        return torch.nn.functional.embedding(args['indices'], args['weight'])
    if op == 'add':
        alpha = float(attrs.get('alpha', 1))
        if not math.isfinite(alpha):
            raise ValueError('add alpha 须为有限数')
        return torch.add(args['a'], args['b'], alpha=alpha)
    return {'mul': torch.mul, 'matmul': torch.matmul}[op](args['a'], args['b'])


# These are scalar defaults of the named PyTorch APIs, not defaults of model-specific wrappers.
# Optional tensor arguments (e.g. bias, mask) may be present in slots even with an empty scalar_args.
EMBEDDING_DEFAULTS = {'padding_idx': None, 'max_norm': None, 'norm_type': 2, 'scale_grad_by_freq': False,
                      'sparse': False}
STANDARD_DEFAULTS = {
    'Torch.add': {'alpha': 1}, 'Tensor.add': {'alpha': 1},
    'Functional.softmax': {'dim': None, 'dtype': None}, 'Torch.softmax': {'dtype': None},
    'Tensor.softmax': {'dtype': None}, 'Functional.silu': {'inplace': False},
    'Functional.gelu': {'approximate': 'none'},
    'Functional.layer_norm': {'eps': Decimal('1e-5')}, 'Torch.layer_norm': {'eps': Decimal('1e-5')},
    'Functional.rms_norm': {'eps': None}, 'Torch.rms_norm': {'eps': None},
    'Functional.embedding': EMBEDDING_DEFAULTS,
    'Functional.scaled_dot_product_attention': {'dropout_p': 0, 'scale': None, 'is_causal': False,
                                              'enable_gqa': False},
}


def observed_scalars(boundary):
    values = boundary.get('scalar_args')
    if values is None:
        return {}
    defaults = STANDARD_DEFAULTS.get(boundary['name'], {})
    return {**defaults, **values}


def expected_scalars(spec):
    op = spec['op']
    defaults = {'add': {'alpha': 1, 'out': None}, 'mul': {'out': None}, 'matmul': {'out': None},
                'softmax': {'dtype': None}, 'embedding': EMBEDDING_DEFAULTS}
    expected = {**defaults.get(op, {}), **spec.get('attributes', {})}
    for arg in ('bias', 'weight'):
        if op in ('linear', 'layer_norm') and arg not in spec['inputs']:
            expected[arg] = None
    if op == 'scaled_dot_product_attention':
        expected['enable_gqa'] = False  # CPU 参考接受已经按 head 对齐的稠密 Q/K/V。
        native_causal = {'upper_left': True, 'none': False}
        if expected.get('causal_alignment') in native_causal:
            expected['is_causal'] = native_causal[expected['causal_alignment']]
    return expected


def verify_scalars(call, spec):
    expected = expected_scalars(spec)
    for side in ('gpu', 'npu'):
        for key, value in observed_scalars(call[side]).items():
            # SiLU's inplace flag affects storage. Its pre-hook captured the original input.
            if spec['op'] == 'silu' and key == 'inplace' and type(value) is bool:
                continue
            if key not in expected or value != expected[key] or isinstance(value, bool) != isinstance(expected[key], bool):
                raise ValueError(f'{side} 实际标量 {key}={value!r} 与 CPU 参考参数不匹配')


def arguments(spec, slots):
    op = spec['op']
    if op not in SIGNATURES:
        raise ValueError('没有此算子的 CPU 参考实现：' + str(op))
    required, optional, allowed_attrs = SIGNATURES[op]
    names, attrs = spec['inputs'], spec.get('attributes', {})
    if not required <= names.keys() <= required | optional or attrs.keys() - allowed_attrs:
        raise ValueError('CPU 算子的必要参数缺失或存在未知参数')
    planned = {s['id'] for s in slots if s['role'] != 'output'}
    if set(names.values()) != planned:
        raise ValueError('CPU 输入绑定未覆盖本次调用的全部输入、权重或状态')
    outputs = {s['id'] for s in slots if s['role'] == 'output'}
    if spec['output'] not in outputs:
        raise ValueError('CPU 参考未绑定声明的目标输出')
    return names, attrs


def promoted(names, values, torch):
    return {name: values[slot].to(torch.float64) if values[slot].is_floating_point() else values[slot]
            for name, slot in names.items()}


def same_input(left, right, torch):
    return left.dtype == right.dtype and left.shape == right.shape and torch.equal(left, right)


def output_result(rows):
    outcomes = {row['result'] for row in rows.values()}
    if 'fail' in outcomes:
        return 'mismatch'
    return 'aligned' if outcomes == {'pass'} else 'not_evaluated'


def compute(spec, args, attrs, torch, plan):
    if spec['op'] == 'scaled_dot_product_attention':
        return dense_oracles.attention(args, attrs, torch, plan)
    return evaluate(spec['op'], args, attrs, torch)


def execution(spec, slots, values, limits, torch, plan):
    names, attrs = arguments(spec, slots)
    gpu, npu = values['gpu'], values['npu']
    drift = [slot for slot in names.values() if not same_input(gpu[slot], npu[slot], torch)]
    bases = ('gpu', 'npu') if drift else ('gpu',)
    with torch.inference_mode():
        references = {side: compute(spec, promoted(names, values[side], torch), attrs, torch, plan) for side in bases}
    out = next(slot for slot in slots if slot['id'] == spec['output'])
    rows = {side: metrics(values[side][spec['output']], references[side] if drift else references['gpu'], limits, torch,
                          allow_inf=out.get('allow_matching_inf', False))
            for side in values}
    unchecked = [s['id'] for s in slots if s['role'] == 'output' and s['id'] != spec['output']]
    return {'result': output_result(rows), 'basis': 'per_device_inputs' if drift else 'common',
            'checked_output': spec['output'], 'unassessed_outputs': unchecked,
            'different_inputs': drift, 'op': spec['op'], 'dtype': str(references['gpu'].dtype), 'device': 'cpu', **rows}


def compare(call, values, limits, torch, plan):
    if 'cpu_reference' not in call:
        return {'result': 'not_evaluated', 'reason': '未配置该调用的 CPU 参考'}
    try:
        spec = call['cpu_reference']
        verify_scalars(call, spec)
        slices = any(s.get('capture_slice') or any('slice' in step for steps in s.get('transforms', {}).values()
                     for step in steps) for s in call['slots'])
        if slices and spec['op'] not in ('add', 'mul', 'silu', 'gelu'):
            return {'result': 'not_evaluated', 'reason': '所采切片未证明包含 CPU 参考计算的完整依赖'}
        return execution(spec, call['slots'], values, limits, torch, plan)
    except (ImportError, AttributeError, TypeError, KeyError, ValueError, RuntimeError, IndexError) as error:
        return {'result': 'not_evaluated', 'reason': str(error)}
