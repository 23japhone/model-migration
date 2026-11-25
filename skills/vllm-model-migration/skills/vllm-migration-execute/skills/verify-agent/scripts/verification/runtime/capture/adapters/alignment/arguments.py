"""从实际框架参数复核 CPU 语义所依赖的标量与操作数覆盖；仅检查元数据。"""

# Names follow the observed PyTorch callable. Their order includes keyword-only arguments
# where defaults may be confirmed even if the caller did not explicitly pass a value.
SIGNATURES = {
    'Torch.add': (('input', 'other'), {'alpha': 1, 'out': None}),
    'Torch.mul': (('input', 'other'), {'out': None}),
    'Torch.matmul': (('input', 'other'), {'out': None}),
    'Functional.linear': (('input', 'weight', 'bias'), {'bias': None}),
    'Functional.silu': (('input', 'inplace'), {'inplace': False}),
    'Functional.softmax': (('input', 'dim', '_stacklevel', 'dtype'), {'dim': None, '_stacklevel': 3, 'dtype': None}),
}


def tensor_paths(value, location):
    import torch
    if isinstance(value, torch.Tensor):
        yield location
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from tensor_paths(item, (*location, key))
    elif isinstance(value, (tuple, list)):
        for i, item in enumerate(value):
            yield from tensor_paths(item, (*location, i))


def supplied(io, positional_names):
    for index, value in enumerate(io.args or ()):
        name = positional_names[index] if index < len(positional_names) else None
        yield name, ('input_args', index), value
    for key, value in (io.kwargs or {}).items():
        yield key, ('input_kwargs', key), value


def scalar_matches(value, expected):
    if isinstance(value, bool) or isinstance(expected, bool):
        return value is expected
    if value is None or isinstance(value, (int, float, str)):
        return value == expected
    return False


def verify_arguments(call, hardware, io):
    declaration = call[hardware]
    expected = declaration.get('scalar_args')
    complete = declaration.get('arguments_complete', False)
    if type(complete) is not bool:
        raise ValueError('arguments_complete 须为布尔值')
    if expected is None and not complete:
        return {}
    if expected is not None and not isinstance(expected, dict):
        raise ValueError('声明的标量参数须为字典')
    names, defaults = SIGNATURES.get(declaration['name'], ((), {}))
    paths = {tuple(slot[hardware]) for slot in call['slots'] if slot['role'] != 'output'}
    actual = list(supplied(io, names))
    if complete:
        check_complete(actual, paths)
    if expected is not None:
        check_scalars(actual, paths, defaults, expected)
    return {key: declaration[key] for key in ('scalar_args', 'arguments_complete') if key in declaration}


def check_complete(actual, selected):
    for _, location, value in actual:
        for path in tensor_paths(value, location):
            if path not in selected:
                raise ValueError('实际调用含未纳入计划的操作数 ' + str(path))


def check_scalars(actual, selected, defaults, expected):
    values = dict(defaults)
    observed = set()
    for name, location, value in actual:
        if name in expected or location not in selected:
            values[name] = value
            observed.add(name)
    for key, value in expected.items():
        if key not in values or not scalar_matches(values[key], value):
            raise ValueError('实际标量与计划不同 ' + str(key))
    for key in observed - expected.keys():
        if key not in defaults or not scalar_matches(values[key], defaults[key]):
            raise ValueError('实际调用包含计划外标量或参数 ' + str(key))
