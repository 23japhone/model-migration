"""官方内容寻址模型快照的只读文件边界，不放宽普通资产的链接规则。"""
from pathlib import Path
import re


def repository_root(root):
    root = Path(root)
    if root.parent.name != 'snapshots' or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', root.name):
        return None
    repo = root.parent.parent
    hf = repo.name.startswith('models--')
    ms = repo.parent.name == 'models' and '--' in repo.name
    return repo if hf or ms else None


def checked(root, relative):
    root = Path(root)
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts or root.resolve() != root:
        raise ValueError('模型文件路径不规范')
    path = root / name
    resolved = path.resolve()
    if not path.is_file():
        raise ValueError('模型文件不存在')
    if resolved == path:
        return resolved
    repo = repository_root(root)
    if repo is None or resolved.parent != repo / 'blobs':
        raise ValueError('模型链接不属于已支持的官方快照 blobs')
    if not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', resolved.name):
        raise ValueError('模型缓存 blob 名称无效')
    return resolved
