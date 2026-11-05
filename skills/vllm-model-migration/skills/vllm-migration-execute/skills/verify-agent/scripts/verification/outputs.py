"""所有公开操作的安装资源、模型资产与构建输入保护边界。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import re


def protected_roots(request):
    if not isinstance(request, dict):raise ValueError('来源保护根对象无效')
    result = []
    for section, field in [('model', 'root'), ('registration', 'resources'), ('build', 'context')]:
        value = request.get(section, {})
        if not isinstance(value, dict):raise ValueError('来源保护根对象无效')
        if field in value:
            path = value[field]
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError('来源保护根须为绝对路径')
            result.append(Path(path))
    return result


def check(target, protected=()):
    target = Path(target)
    if target.resolve() != target:raise ValueError('输出目录不能通过符号链接重定向')
    # 完整开发包还须保护 skills 之外的清单、校验器和说明。
    installed = release_root()
    roots = [installed, *protected]
    for root in roots:
        root = Path(root).resolve()
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError('输出须在完整技能资源、模型资产和构建输入之外')
    return target


def target(work_root, run_id, protected=()):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}', run_id):raise ValueError('run-id 无效')
    path = Path(work_root).expanduser().resolve() / run_id / 'verify-agent'
    return check(path, protected)
