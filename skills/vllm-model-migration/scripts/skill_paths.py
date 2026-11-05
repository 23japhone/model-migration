"""完整总 Skill 的路径与资源归属；不依赖维护仓库或调用时 cwd。"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys

sys.dont_write_bytecode = True

RESOURCE_ROLES = {
    'scripts': {'.py', '.sh'},
    'references': {'.md', '.json'},
    'reference': {'.md', '.json'},
    'assets': {'.json', '.py', '.template'},
}
IGNORED_NAMES = {'.git', '.agents', '.codex', '.venv', '__pycache__', '.pytest_cache',
                 '.mypy_cache', '.ruff_cache', '.DS_Store'}
IGNORED_SUFFIXES = {'.pyc', '.pyo', '.log'}
SHARED_REQUIRED = ('scripts/skill_paths.py', 'scripts/device_contract.py', 'references/skill-layout.json',
                   'scripts/allocation_contract.py', 'scripts/device_evidence.py',
                   'references/task-result.schema.json')
EXTRA_REQUIRED = {
    'vllm-model-migration': ('scripts/export_handoff.py', 'scripts/check_evalscope_handoff.py', 'scripts/install_evalscope.py',
                             'scripts/evalscope_installation.py', 'references/evalscope-installation.md'),
    'vllm-dataset-prepare': ('references/recipes.json',),
    'vllm-model-register': ('scripts/check.py', 'assets/plugin_callback.py.template'),
}


def plugin_root():
    return Path(__file__).resolve().parents[1]


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        raise ValueError('发布路径无效')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value or value == '.':
        raise ValueError('发布路径越界或未规范化')
    return value


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('发布索引重复字段')
        value[key] = item
    return value


def read_index(name, root=None):
    def invalid(value):
        raise ValueError('发布索引含非有限数字')
    return json.loads(contained('references/'+relative(name), root).read_text(encoding='utf-8'),
                      object_pairs_hook=unique, parse_constant=invalid)


def contained(value, root=None):
    root = plugin_root() if root is None else Path(root).resolve()
    path = root/relative(value)
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('发布路径经链接重定向或越界')
    return path


def skill_root(skill_name, root=None):
    root = plugin_root() if root is None else Path(root).resolve()
    value = read_index('skill-layout.json', root)
    if skill_name not in value['skills']:
        raise ValueError('未安装的 Skill：' + str(skill_name))
    path = value['skills'][skill_name]['path']
    if skill_name == value['root'] and path == '.':
        return root
    return contained(path, root)


def shared_resource(relative_path):
    path = skill_root(read_index('skill-layout.json')['root'])/relative(relative_path)
    return contained(path.relative_to(plugin_root()).as_posix())


def _kind(path):
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        raise ValueError('静态资源不能包含符号链接：'+path.name)
    if stat.S_ISDIR(mode):
        return 'directory'
    if stat.S_ISREG(mode):
        return 'file'
    raise ValueError('静态资源不能包含特殊文件：'+path.name)


def _walk_resources(directory, extensions):
    files = []
    for path in sorted(directory.iterdir()):
        kind = _kind(path)
        if path.name in IGNORED_NAMES or path.suffix in IGNORED_SUFFIXES:
            continue
        if kind == 'directory':
            files.extend(_walk_resources(path, extensions))
        elif extensions is None or path.suffix in extensions:
            files.append(path)
        else:
            raise ValueError('静态资源类型不支持：'+path.name)
    return files


def _definitions(root):
    layout = read_index('skill-layout.json', root)
    definitions = {}
    for name, row in layout['skills'].items():
        path = root if name == layout['root'] and row['path'] == '.' else contained(row['path'], root)
        definitions[name] = (row, path)
    if layout['root'] not in definitions or len({p for _, p in definitions.values()}) != len(definitions):
        raise ValueError('Skill 布局缺少总入口或存在重复资源根')
    if definitions[layout['root']][1] != root:
        raise ValueError('布局总入口必须指向完整资源根')
    return definitions


def _require_file(path):
    if _kind(path) != 'file':
        raise ValueError('缺少必需静态文件：'+path.name)


def _direct_resources(name, row, skill, root):
    for relative_path in SHARED_REQUIRED:
        _require_file(contained(relative_path, root))
    required = ['SKILL.md', row['entrypoint']['script'], *EXTRA_REQUIRED.get(name, ())]
    for relative_path in required:
        _require_file(contained((skill/relative(relative_path)).relative_to(root).as_posix(), root))
    files = [skill/'SKILL.md']
    for role, extensions in RESOURCE_ROLES.items():
        directory = skill/role
        if directory.exists() or directory.is_symlink():
            if _kind(directory) != 'directory':
                raise ValueError('静态资源角色必须是目录：'+role)
            files.extend(_walk_resources(directory, extensions))
    if not {skill/p for p in required}.issubset(files):
        raise ValueError('必需入口不在允许的静态资源目录中')
    return sorted(files)


def owned_files(path):
    """枚举当前直属静态资源；父 Skill 不收入嵌套子 Skill。"""
    root = plugin_root()
    path = Path(path)
    if path != root:
        path = contained(path.relative_to(root).as_posix())
    definitions = _definitions(root)
    matches = [(name, row, skill) for name, (row, skill) in definitions.items()
               if path.is_relative_to(skill)]
    if not matches:
        raise ValueError('路径不属于已声明的 Skill')
    name, row, skill = max(matches, key=lambda item: len(item[2].parts))
    files = [p for p in _direct_resources(name, row, skill, root) if p.is_relative_to(path)]
    if not files:
        raise ValueError('路径不是允许的静态资源')
    return files


def _check_children(skill, declared):
    directory = skill/'skills'
    if directory.exists() or directory.is_symlink():
        if _kind(directory) != 'directory':
            raise ValueError('嵌套 skills 必须是目录')
        for path in sorted(directory.iterdir()):
            _kind(path)
            if path.name not in IGNORED_NAMES and path not in declared:
                raise ValueError('嵌套 Skill 未在布局中声明：'+path.name)


def bundle_files(root=None):
    """按布局与资源角色枚举完整运行副本，不依赖构建清单。"""
    root = plugin_root() if root is None else Path(root).resolve()
    definitions = _definitions(root)
    declared = {path for _, path in definitions.values()}
    files = []
    for name, (row, skill) in definitions.items():
        _check_children(skill, declared)
        files.extend(_direct_resources(name, row, skill, root))
    if len(files) != len(set(files)):
        raise ValueError('静态资源归属重复')
    return sorted(files)


def registration_files(root):
    """嵌套资源用统一规则；旧平铺资源保留普通文件兼容。"""
    root = Path(root).resolve()
    if (root/'references/skill-layout.json').is_file():
        return bundle_files(root)
    return _walk_resources(root, None)


def resource_snapshot(files, root=None):
    """记录所给普通文件的实际字节，不写入技能资源。"""
    root = plugin_root() if root is None else Path(root).resolve()
    result = {}
    for path in sorted(set(files)):
        key = Path(path).relative_to(root).as_posix()
        path = contained(key, root)
        _require_file(path)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
        result[key] = digest.hexdigest()
    return result


def verify_snapshot(files, expected, root=None):
    if resource_snapshot(files, root) != expected:
        raise ValueError('静态资源在记录或复制时发生变化')


def registration_bundle(root):
    """接受完整嵌套包或既有平铺注册资源，不把安装路径当作容器路径。"""
    root = Path(root).resolve()
    if root.name == 'skills' and (root.parent/'references/skill-layout.json').is_file():
        root = root.parent
    if (root/'references/skill-layout.json').is_file():
        expected = skill_root('vllm-model-register', root).relative_to(root)/'scripts/check.py'
    else:
        expected = Path('vllm-model-register/scripts/check.py')
    if not contained(expected.as_posix(), root).is_file():
        raise ValueError('缺少注册公开检查入口')
    return root, expected.as_posix()
