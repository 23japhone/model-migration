"""对已绑定 rank 的快照逐文件检查，保留同层中其他可读原值。"""
from ..cache_evidence import REQUIRED, OPTIONAL, verify_files
from ..layer_evidence import BOUNDARY

ALLOWED = REQUIRED | OPTIONAL | {'ssm.initial'}


def inspect_group(directory, snapshots, allowed):
    errors = []
    for name, item in list(snapshots.items()):
        try:
            verify_files(directory, {name: item}, allowed)
        except (ValueError, OSError, KeyError, TypeError) as error:
            del snapshots[name]
            errors.append(f'{name}: {error}')
    return errors


def isolate_files(directory, cache):
    errors = []
    for layer in cache.get('layers', []):
        reasons = []
        for call in layer['invocations']:
            base = directory / f"layer-{layer['index']}" / f"call-{call['occurrence']}"
            reasons.extend(inspect_group(base, call['boundary'], BOUNDARY))
            for i, state in enumerate(call['states']):
                reasons.extend(inspect_group(base / f'state-{i}', state['snapshots'], ALLOWED))
        if reasons:
            layer['status'] = cache['status'] = 'incomplete'
            messages = [f"{layer['path']} {reason}" for reason in reasons]
            layer['errors'].extend(messages)
            cache['errors'].extend(messages)
            errors.extend(messages)
    return errors
