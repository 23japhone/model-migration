"""静态表的控制流与引用边界；不能证明无修改时拒绝完整性。"""
import ast


READONLY_METHODS = {'items', 'keys', 'values', 'get', 'copy'}


def assignment_targets(node):
    if isinstance(node, (ast.Assign, ast.Delete)):
        return node.targets
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return [node.target]
    return []


def assigned_names(node):
    return {child.id for target in assignment_targets(node) for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}


def retains_table(node, tables):
    """仅追踪原对象或承载它的容器，items/copy 和新推导式不是原 dict。"""
    if isinstance(node, ast.Name):
        return node.id in tables
    if isinstance(node, (ast.Subscript, ast.Attribute, ast.Starred, ast.NamedExpr)):
        return retains_table(node.value, tables)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(retains_table(item, tables) for item in node.elts)
    if isinstance(node, ast.Dict):
        # ** 展开复制条目；显式 dict value 则可能承载原对象。
        return any(key is not None and retains_table(value, tables)
                   for key, value in zip(node.keys, node.values))
    if isinstance(node, ast.IfExp):
        return retains_table(node.body, tables) or retains_table(node.orelse, tables)
    if isinstance(node, ast.BoolOp):
        return any(retains_table(item, tables) for item in node.values)
    return False


def module_definitions(tree):
    definitions = {}
    # 依赖来自模块级定义，推导式局部变量不等同于函数中的同名变量。
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        for name in assigned_names(node):
            definitions.setdefault(name, []).append(node.value)
    return definitions


def registry_tables(tree):
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))]
    definitions = module_definitions(tree)
    tables = {'_VLLM_MODELS'}
    previous = set()
    while previous != tables:
        previous = set(tables)
        for name, values in definitions.items():
            if name in previous:
                tables.update(child.id for value in values for child in ast.walk(value)
                              if isinstance(child, ast.Name) and child.id in definitions)
        # 引用可进入嵌套作用域，记录每次绑定，不只保留最后一次 alias。
        for node in assignments:
            if retains_table(node.value, previous):
                tables.update(assigned_names(node))
    return tables


def reject_call(node, tables):
    if isinstance(node.func, ast.Attribute) and retains_table(node.func.value, tables):
        if node.func.attr not in READONLY_METHODS:
            raise ValueError('注册映射有动态调用，需采用目标版本专用的只读检查')
    arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
    if any(retains_table(value, tables) for value in arguments):
        raise ValueError('注册映射引用传入未知调用，无法证明未被修改')


def reject_binding(node, tables, direct):
    targets = assignment_targets(node)
    if isinstance(node, (ast.Delete, ast.AugAssign)):
        changed = any(retains_table(value, tables) for value in targets)
    else:
        changed = any(isinstance(value, ast.Subscript) and retains_table(value, tables)
                      for value in targets)
    if changed:
        raise ValueError('注册映射有动态条目修改，无法证明完整性')
    if assigned_names(node) & tables and node not in direct:
        raise ValueError('注册映射绑定位于未知控制流或嵌套作用域，无法完整静态解析')


def reject_import(node, tables):
    names = {item.asname or item.name.split('.')[0] for item in node.names}
    if names & tables:
        raise ValueError('注册映射绑定被 import 替换，无法完整静态解析')


def reject_reference_storage(node, tables):
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and retains_table(node.value, tables):
        if any(isinstance(target, (ast.Attribute, ast.Subscript)) for target in assignment_targets(node)):
            raise ValueError('注册映射引用保存到未知对象，无法证明未被修改')
    if isinstance(node, (ast.For, ast.AsyncFor)) and retains_table(node.iter, tables):
        raise ValueError('注册映射引用进入未知循环，无法证明未被修改')


def reject_dynamic_updates(tree):
    tables = registry_tables(tree)
    direct = set(tree.body)
    for node in ast.walk(tree):
        reject_binding(node, tables, direct)
        reject_reference_storage(node, tables)
        if isinstance(node, ast.Call):
            reject_call(node, tables)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            reject_import(node, tables)
        if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) and retains_table(node.value, tables):
            raise ValueError('注册映射引用从当前作用域逃逸，需专用只读检查')
