"""每个物理 step 索引一次，各成员引用同层的原始调用。"""

from ....evidence import read_bound, write_json
from ..adapters.coverage import verify
from ..adapters.state import read
from ..tensors import arguments, call_arguments, file_ref, literals, module_path, owner, tensor_only


def read_calls(dump, ready, frame):
    parents = read(dump / 'construct.json')
    errors, calls, counts = {}, [], {}
    for name, data in read(dump / 'dump.json')['data'].items():
        layer = owner(name, parents, ready['layers'])
        module = module_path(name)
        api = None if module else name.rsplit('.', 2)[0]
        key = (layer, module or api)
        occurrence = counts.get(key, 0)
        counts[key] = occurrence + 1
        try:
            missing = []
            args = call_arguments(name, data, dump, 0, module == layer and bool(layer), missing)
            if module and data.get('parameters'):
                args['parameters'] = list(arguments(data['parameters'], ['parameters'], dump, 0, False, missing))
            if missing:
                errors.setdefault(layer or '', []).extend(name + ' ' + problem for problem in missing)
            calls.append(
                dict(
                    call=name,
                    layer=layer,
                    module=module,
                    api=api,
                    parent=parents.get(name),
                    occurrence=occurrence,
                    tensors=tensor_only(args),
                    values=literals(args),
                )
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.setdefault(layer or '', []).append(name + ' ' + str(error))
    try:
        verify(frame['coverage'], calls)
    except (ValueError, KeyError, TypeError) as error:
        errors.setdefault('', []).append(str(error))
    return calls, errors


def save_layers(group, ready, fid, calls):
    layers = {}
    for i, name in enumerate(ready['layers']):
        part = group / f'native-{fid}-{i}.json'
        entries = [{k: v for k, v in call.items() if k != 'values'} for call in calls if call['layer'] == name]
        write_json(part, dict(group_id=group.name, forward_id=fid, layer=name, calls=entries))
        layers[name] = file_ref(part, group)
    return layers


def load(group, ready, frame):
    fid = frame['forward_id']
    path = group / ('catalog-' + fid + '.json')
    identity = dict(group_id=group.name, forward_id=fid, step=frame['step'])
    bound = group / ('catalog-binding-' + fid + '.json')
    if bound.exists():
        ref = read(bound)
        if ref['path'] != str(path):
            raise ValueError('物理前向的原件清单路径不同')
        result = read_bound(ref)
        if any(result[k] != v for k, v in identity.items()):
            raise ValueError('物理张量目录绑定了其他前向')
        return result
    dump = group.parent / frame['dump']
    if dump != group.parent / 'dump' / 'native' / f'step{frame["step"]}' / 'rank0' or dump.resolve() != dump:
        raise ValueError('msProbe 的实际 step 或 rank 与物理前向不同')
    owner_file = group.parent / f'joint-step-{frame["step"]}-owner.json'
    if owner_file.exists() and read(owner_file) != identity:
        raise ValueError('同一 msProbe step 已绑定其他物理前向')
    write_json(owner_file, identity)
    calls, errors = read_calls(dump, ready, frame)
    result = dict(
        **identity,
        layers=save_layers(group, ready, fid, calls),
        errors=errors,
        bytes=sum(p.stat().st_size for p in dump.rglob('*') if p.is_file() and not p.is_symlink()),
        metadata=[file_ref(dump / n, dump) for n in ('construct.json', 'dump.json')],
    )
    write_json(path, result)
    write_json(bound, file_ref(path, group))
    return result


def calls_for(ref, row, layer):
    value = read_bound(ref)
    if (value['group_id'], value['forward_id'], value['layer']) != (row['group_id'], row['forward_id'], layer):
        raise ValueError('msProbe 层文件身份不符')
    return value['calls']
