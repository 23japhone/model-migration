"""以固定内存占用扫描文件，并建立稳定的内容身份。"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .request import relative_file


SDK_FILES = {".msc", ".mv", ".mdl"}
SDK_DIRS = {".git", "__pycache__", "._____temp"}
CACHE_PREFIXES = ((".cache", "huggingface"), (".cache", "modelscope"))


def ignored(relative):
    return relative.parts[:2] in CACHE_PREFIXES or (len(relative.parts) == 1 and relative.name in SDK_FILES)


def allowed_blob_root(root):
    from model_cache import repository_root
    repository = repository_root(root)
    return repository / 'blobs' if repository is not None else None


def checked_file(root, name):
    relative_file(name)
    target = root / name
    resolved = target.resolve()
    blob_root = allowed_blob_root(root)
    if root != resolved and root not in resolved.parents:
        if blob_root is None or resolved.parent != blob_root or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", resolved.name):
            raise ValueError(f"资产解析后的路径超出允许目录：{name}")
    if not target.is_file():
        raise ValueError(f"模型文件缺失或不是普通文件：{name}")
    return target


def enumerate_files(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("模型目录不存在")
    result = []
    for directory, subdirs, files in os.walk(root, followlinks=False, onerror=raise_walk_error):
        current = Path(directory)
        subdirs[:] = [name for name in subdirs if name not in SDK_DIRS and not ignored((current / name).relative_to(root))]
        for name in subdirs:
            if (current / name).is_symlink():
                raise ValueError(f"不支持目录符号链接：{name}")
        for name in files:
            relative = (current / name).relative_to(root)
            if not ignored(relative):
                checked_file(root, relative.as_posix())
                result.append(relative.as_posix())
    return sorted(result)


def raise_walk_error(error):
    raise ValueError(f"无法遍历模型目录：{error}") from error


def file_state(path):
    info = path.stat()
    link = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"模型资产不是普通文件：{path.name}")
    return (link.st_ino, link.st_mtime_ns, info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def hash_file(path):
    before = file_state(path)
    size = before[4]
    sha256 = hashlib.sha256()
    git_sha1 = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
    count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            sha256.update(chunk)
            git_sha1.update(chunk)
            count += len(chunk)
    if before != file_state(path) or size != count:
        raise ValueError(f"模型文件在计算摘要期间变化：{path.name}")
    return {"size": size, "sha256": sha256.hexdigest(), "git_blob_sha1": git_sha1.hexdigest()}


def verify_record(record, expected, name):
    for field in ("size", "sha256", "git_blob_sha1"):
        if expected.get(field) is not None and record[field] != expected[field]:
            label = "校验摘要" if field != "size" else "大小"
            raise ValueError(f"{name} 的{label}不一致")


def scan(root, names, expected=None):
    root = Path(root).resolve()
    available_before = enumerate_files(root)
    before = {name: file_state(checked_file(root, name)) for name in names}
    files = []
    for name in sorted(names):
        record = {"path": name, **hash_file(checked_file(root, name))}
        verify_record(record, (expected or {}).get(name, {}), name)
        files.append(record)
    after = {name: file_state(checked_file(root, name)) for name in names}
    if before != after or available_before != enumerate_files(root):
        raise ValueError("模型文件在生成清单期间变化，未记录稳定的内容身份")
    return files


def content_identity(files):
    payload = [{key: item[key] for key in ("path", "size", "sha256")} for item in sorted(files, key=lambda item: item["path"])]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {"format": "vllm-model-file-list-v1", "sha256": hashlib.sha256(encoded).hexdigest()}
