"""检查 Docker save 归档，不向主机目录解包。"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def file_digest(path):
    hashed = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hashed.update(block)
    return hashed.hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    if not name or '\\' in name or path.is_absolute() or '..' in path.parts or path.as_posix() != name:
        raise ValueError('镜像归档成员必须采用规范化的相对路径')


def read_member(tar, members, name, limit=4 * 1024 * 1024):
    safe_member(name)
    member = members.get(name)
    if member is None or not member.isfile() or member.size > limit:
        raise ValueError(f'归档必需资料缺失、过大或不是文件：{name}')
    with tar.extractfile(member) as stream:
        return stream.read(limit + 1)


def manifest_entry(tar, members):
    try:
        rows = json.loads(read_member(tar, members, 'manifest.json'))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError('Docker save 归档 manifest.json 无效') from error
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('仅加载一份明确镜像；多镜像归档请重新导出所需镜像')
    row = rows[0]
    if not isinstance(row.get('Config'), str) or not isinstance(row.get('Layers'), list):
        raise ValueError('归档必须包含 Config 和 Layers')
    tags = row.get('RepoTags') or []
    if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
        raise ValueError('归档 RepoTags 格式无效')
    from .request import reference
    for tag in tags:
        reference(tag)
    return row, tags


def check_layers(tar, members, row, config):
    rootfs = config.get('rootfs')
    if not isinstance(rootfs, dict) or rootfs.get('type') != 'layers':
        raise ValueError('归档配置缺少 rootfs 层身份')
    hashes = rootfs.get('diff_ids')
    if not isinstance(hashes, list) or len(hashes) != len(row['Layers']):
        raise ValueError('归档配置与 Layers 数量不一致')
    for name, expected in zip(row['Layers'], hashes):
        if not isinstance(name, str):
            raise ValueError('归档 Layers 路径无效')
        safe_member(name)
        member = members.get(name)
        if member is None or not member.isfile():
            raise ValueError(f'归档镜像层缺失：{name}')
        hashed = hashlib.sha256()
        with tar.extractfile(member) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hashed.update(block)
        if expected != 'sha256:' + hashed.hexdigest():
            raise ValueError(f'归档镜像层摘要不匹配：{name}')


def inspect_archive(path, expected_id, expected_sha256=None):
    path = Path(path)
    before = path.stat()
    hashed = file_digest(path)
    if expected_sha256 and hashed != expected_sha256:
        raise ValueError('镜像归档 SHA256 与预期不一致')
    try:
        with tarfile.open(path, 'r:*') as tar:
            members = {}
            for member in tar:
                safe_member(member.name)
                if member.name in members or not (member.isfile() or member.isdir()):
                    raise ValueError('归档包含重复成员、链接或特殊文件')
                members[member.name] = member
            row, tags = manifest_entry(tar, members)
            raw = read_member(tar, members, row['Config'])
            identity = 'sha256:' + hashlib.sha256(raw).hexdigest()
            if identity != expected_id:
                raise ValueError('归档 image ID 与预期不一致')
            config = json.loads(raw)
            if not isinstance(config, dict):
                raise ValueError('归档镜像配置必须为对象')
            check_layers(tar, members, row, config)
    except (tarfile.TarError, json.JSONDecodeError, UnicodeError) as error:
        raise ValueError('归档必须是完整的 Docker save 格式，不能使用 docker export 或未支持的 OCI 布局') from error
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('镜像归档在核验期间变化')
    platform = '/'.join(str(config.get(k, '')) for k in ('os', 'architecture'))
    if config.get('variant'):
        platform += '/' + config['variant']
    return {'path': str(path), 'sha256': hashed, 'size': before.st_size, 'image_id': identity,
            'platform': platform, 'tags': tags, 'layers': len(row['Layers'])}
