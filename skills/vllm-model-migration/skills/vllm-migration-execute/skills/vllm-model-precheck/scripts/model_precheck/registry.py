"""以受限 AST 解释常量注册表，不导入或执行目标源码。"""
import ast
import hashlib
from pathlib import Path

from .evidence import file_record, identity, read_bytes
from .registry_safety import reject_dynamic_updates


def expression(node, names):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(expression(item, names) for item in node.elts)
    if isinstance(node, ast.Dict):
        value = {}
        for key, item in zip(node.keys, node.values):
            if key is None:
                value.update(expression(item, names))
            else:
                value[expression(key, names)] = expression(item, names)
        return value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return expression(node.left, names) | expression(node.right, names)
    if isinstance(node, ast.DictComp):
        return comprehension(node, names)
    if isinstance(node, ast.Compare):
        return comparison(node, names)
    raise ValueError('注册映射含不支持的动态表达式')


def comparison(node, names):
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        raise ValueError('注册表仅支持常量的单项相等比较')
    equal = expression(node.left, names) == expression(node.comparators[0], names)
    return equal if isinstance(node.ops[0], ast.Eq) else not equal


def comprehension(node, names):
    if len(node.generators) != 1 or node.generators[0].is_async:
        raise ValueError('注册表仅支持单层常量 dict 推导式')
    generator = node.generators[0]
    items = constant_items(generator.iter, names)
    output = {}
    for key, value in items:
        local = dict(names)
        bind_names(generator.target, (key, value), local)
        if all(expression(condition, local) for condition in generator.ifs):
            output[expression(node.key, local)] = expression(node.value, local)
    return output


def constant_items(node, names):
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        raise ValueError('推导式必须遍历常量 dict.items()')
    if not isinstance(node.func, ast.Attribute) or node.func.attr != 'items':
        raise ValueError('推导式不允许调用外部函数')
    value = expression(node.func.value, names)
    if not isinstance(value, dict) or len(value) > 10000:
        raise ValueError('推导式遍历范围无效或过大')
    return value.items()


def bind_names(target, value, names):
    if isinstance(target, ast.Name):
        names[target.id] = value
        return
    if not isinstance(target, ast.Tuple) or not isinstance(value, tuple) or len(target.elts) != len(value):
        raise ValueError('推导式变量必须为常量元组解构')
    for child, item in zip(target.elts, value):
        bind_names(child, item, names)


def parse_registry(path):
    raw = read_bytes(path)
    tree = ast.parse(raw.decode('utf-8-sig'))
    reject_dynamic_updates(tree)
    mapping = constant_names(tree).get('_VLLM_MODELS')
    validate_mapping(mapping)
    return mapping, hashlib.sha256(raw).hexdigest()


def constant_names(tree):
    names = {}
    for statement in tree.body:
        targets = []
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        if not targets or statement.value is None:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                names[target.id] = expression(statement.value, names)
            except (ValueError, KeyError, TypeError, AttributeError):
                names.pop(target.id, None)
    return names


def validate_mapping(mapping):
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('无法完整静态解析 _VLLM_MODELS，不能推断未注册')
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, tuple) or len(value) != 2:
            raise ValueError('注册映射结构无法识别')
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError('注册模块和 class 标识无效')


def source_inventory(source):
    from .source_identity import projection
    root = Path(source['path'])
    if not root.is_dir():
        raise ValueError('源码目录不存在')
    files = []
    for path in sorted(root.rglob('*.py')):
        if any(part in {'.git', '__pycache__', '.venv'} for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('源码包含越界符号链接')
        if len(files) >= 10000:
            raise ValueError('源码文件数量超过预检上限')
        record = file_record(path)
        record['path'] = path.relative_to(root).as_posix()
        files.append(record)
    if not files:
        raise ValueError('源码目录没有可核对的 Python 文件')
    digest = identity(files)
    if source.get('expected_identity', digest) != digest:
        raise ValueError('源码内容身份不符合 expected_identity')
    return {'path': str(root), **projection(source), 'identity_format': 'precheck-python-file-list-v1',
            'identity': digest, 'files': files}


def inspect_class(path, class_name):
    raw = read_bytes(path)
    tree = ast.parse(raw.decode('utf-8-sig'))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    if not classes:
        raise ValueError('源码中没有声明所需 class：' + class_name)
    node = classes[0]
    return {'path': str(path), 'class_name': class_name, 'sha256': hashlib.sha256(raw).hexdigest(),
            'methods': [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))],
            'import_validation': 'not_evaluated'}


def module_source_path(root, module):
    parts = module.split('.')
    if any(not part.isidentifier() for part in parts):
        raise ValueError('注册模块标识无效')
    stem = root.joinpath(*parts)
    candidates = [path for path in (stem.with_suffix('.py'), stem / '__init__.py') if path.is_file()]
    if len(candidates) != 1:
        raise ValueError('注册模块必须唯一对应模块文件或包入口')
    path = candidates[0]
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('注册模块路径为符号链接')
    if not path.resolve().is_relative_to(root):
        raise ValueError('注册模块路径越界或为符号链接')
    return path.resolve()


def inspect_registry(source, architectures):
    root = Path(source['path']) / 'vllm/model_executor/models'
    result = {'complete': False, 'entries': [], 'errors': []}
    try:
        mapping, digest = parse_registry(root / 'registry.py')
        result.update(complete=True, registry_sha256=digest, architecture_count=len(mapping))
    except (OSError, ValueError, SyntaxError, RecursionError) as error:
        result['errors'].append(str(error))
        return result
    for architecture in architectures:
        entry = {'architecture': architecture, 'registered': architecture in mapping}
        if entry['registered']:
            module, class_name = mapping[architecture]
            try:
                path = module_source_path(root, module)
                entry['implementation'] = inspect_class(path, class_name)
            except (OSError, ValueError, SyntaxError) as error:
                entry['error'] = str(error)
        result['entries'].append(entry)
    return result


def inspect_optional(request):
    result = {'implementation': None, 'adaptation': None, 'errors': []}
    if 'implementation' in request:
        value = request['implementation']
        try:
            item = inspect_class(Path(value['path']), value['class_name'])
            result['implementation'] = {**item, 'architecture': value['architecture']}
        except (OSError, ValueError, SyntaxError) as error:
            result['errors'].append('已有实现不可用：' + str(error))
    if 'adaptation' in request:
        value = request['adaptation']
        reference = value['reference']
        try:
            item = inspect_class(Path(reference['path']), reference['class_name'])
            if item['sha256'] != reference['sha256']:
                raise ValueError('参考实现摘要不一致')
            result['adaptation'] = {**item, 'allowed_scope': value['allowed_scope']}
        except (OSError, ValueError, SyntaxError) as error:
            result['errors'].append('适配参考不可用：' + str(error))
    return result
