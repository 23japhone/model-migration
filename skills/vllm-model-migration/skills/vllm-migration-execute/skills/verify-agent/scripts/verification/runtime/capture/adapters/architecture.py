"""由配置和已加载的解码器生成可查询的结构清单；辅助栈另行登记。"""
from collections import Counter

from .decode_cache.backends import BY_CLASS


def class_name(instance):
    cls = type(instance)
    return cls.__module__ + '.' + cls.__qualname__


def cache_component(path, module):
    impl = getattr(module, 'impl', None)
    if impl is not None and hasattr(module, 'layer_name'):
        backend = class_name(impl)
        spec = BY_CLASS.get(backend)
        return {'module': path, 'address': module.layer_name, 'backend': backend,
                'kind': spec.kind if spec else 'unsupported'}
    backend = class_name(module)
    spec = BY_CLASS.get(backend)
    if spec and spec.direct_address:
        return {'module': path, 'address': getattr(module, spec.direct_address), 'backend': backend, 'kind': spec.kind}
    return None


def describe_block(stack, index, block, declared):
    path = f'{stack}.{index}'
    states = []
    modules = dict(block.named_modules())
    for local, module in modules.items():
        component = cache_component(path + '.' + local if local else path, module)
        if component:
            parent = modules.get(local.rpartition('.')[0])
            if component['kind'] == 'mla' and getattr(parent, 'mla_attn', None) is module:
                component['msprobe_module'] = path + '.' + local.rpartition('.')[0] if parent is not block else path
            states.append(component)
    children = {name: class_name(child) for name, child in block.named_children()}
    inferred = 'attention' if any('Attention' in cls for cls in children.values()) else 'unclassified'
    if type(block).__module__ in ('torch.nn.modules.linear', 'torch.nn.modules.normalization'):
        inferred = 'stateless'
    return {'path': path, 'index': index, 'class': class_name(block), 'declared_type': declared,
            'children': children, 'states': states,
            'kind': '+'.join(item['kind'] for item in states) or declared or inferred}


def text_stack(candidates, count):
    matching = [path for path, blocks in candidates.items() if len(blocks) == count]
    # 视觉和专家栈各自有自己的计数，即便刚好与文本层数相等。
    excluded = {'visual', 'vision_model', 'vision_tower', 'experts', 'encoder'}
    text = [path for path in matching if excluded.isdisjoint(path.split('.'))]
    roots = [path for path in text if not any(path.startswith(other + '.') for other in text if other != path)]
    if len(roots) != 1:
        raise ValueError(f'无法唯一绑定配置的 {count} 个文本层，候选栈 {roots}')
    return roots[0]


def block_containers(model):
    import torch.nn as nn
    return {path: module for path, module in model.named_modules()
            if path and isinstance(module, (nn.ModuleList, nn.Sequential)) and len(module)}


def auxiliary_stacks(candidates, stack):
    outside = {p: v for p, v in candidates.items() if p != stack and not p.startswith(stack + '.')}
    roots = [p for p in outside if not any(p.startswith(other + '.') for other in outside if other != p)]
    return {p: [{'index': i, 'path': f'{p}.{i}', 'class': class_name(layer)}
                for i, layer in enumerate(outside[p])] for p in roots}


def discover(model, config):
    candidates = block_containers(model)
    count = config.num_hidden_layers
    stack = text_stack(candidates, count)
    declarations = getattr(config, 'layer_types', None)
    if declarations is not None and (not isinstance(declarations, (tuple, list)) or len(declarations) != count
                                     or any(not isinstance(name, str) or not name for name in declarations)):
        raise ValueError('配置的 layer_types 数量与层数不符')
    blocks = candidates[stack]
    if len({id(block) for block in blocks}) != count:
        raise ValueError('逻辑层共享实例，当前无法唯一绑定每次调用')
    rows = [describe_block(stack, i, block, declarations[i] if declarations else None)
            for i, block in enumerate(blocks)]
    summary = dict(Counter(row['kind'] for row in rows))
    model_name = class_name(model)
    diagram = '\n'.join([model_name, stack] + [f"  {r['index']:>3}  {r['kind']}  {r['class']}" for r in rows])
    aux = auxiliary_stacks(candidates, stack)
    if aux:
        diagram += '\n' + '\n'.join(f'{path}  ({len(layers)})' for path, layers in aux.items())
    return {'model_class': model_name, 'model_type': getattr(config, 'model_type', None), 'stack': stack,
            'declared_count': count, 'layers': rows, 'counts': summary, 'diagram': diagram,
            'other_stacks': aux}
