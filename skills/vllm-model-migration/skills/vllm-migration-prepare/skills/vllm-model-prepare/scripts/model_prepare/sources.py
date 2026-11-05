"""锁定远程文件身份，保护已有文件并获取资产。"""
import hashlib
import json
from pathlib import Path
import re

from .inventory import checked_file, enumerate_files, hash_file, verify_record
from .providers import HuggingFaceSource, ModelScopeSource
from .request import is_metadata, relative_file


def normalize_description(description, request):
    records = description["source_files"]
    if not isinstance(records, list) or not records:
        raise ValueError("远程仓库未返回模型文件")
    names = set()
    for row in records:
        name = relative_file(row["path"])
        if name in names:
            raise ValueError(f"远程文件名重复：{name}")
        names.add(name)
        if type(row.get("size")) is not int or row["size"] < 0:
            raise ValueError(f"远程来源未提供文件大小：{name}")
        validate_digest(row, name)
    if "config.json" not in names:
        raise ValueError("远程仓库缺少 config.json")
    selected = names if request["scope"] == "full" else {name for name in names if is_metadata(name)}
    selected |= set(request["required_files"])
    if not selected <= names:
        raise ValueError(f"远程来源缺少必需文件：{sorted(selected - names)}")
    description["source_files"] = sorted(records, key=lambda row: row["path"])
    description["selected_files"] = sorted(selected)
    encoded = json.dumps(description["source_files"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    description["snapshot_identity"] = hashlib.sha256(encoded).hexdigest()
    return description


def validate_digest(row, name):
    for field, length in (("sha256", 64), ("git_blob_sha1", 40)):
        digest = row.get(field)
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(f"[a-f0-9]{{{length}}}", digest)):
            raise ValueError(f"远程来源的 {field} 无效：{name}")
    if row.get("sha256") is None and row.get("git_blob_sha1") is None:
        raise ValueError(f"远程来源未提供校验摘要：{name}")


def verify_existing(request, description):
    root = Path(request["path"])
    if root.is_symlink():
        raise ValueError("远程下载目标不能是符号链接")
    if not root.exists():
        return set()
    names = set(enumerate_files(root))
    roster = {row["path"]: row for row in description["source_files"]}
    extra = names - roster.keys()
    if extra:
        raise ValueError(f"下载目录包含所选仓库中不存在的文件：{sorted(extra)[:5]}")
    present = names & set(description["selected_files"])
    for name in sorted(present):
        verify_record(hash_file(checked_file(root.resolve(), name)), roster[name], name)
    return present


def prepare_remote(request, on_snapshot=None):
    provider = HuggingFaceSource(request) if request["source"] == "huggingface" else ModelScopeSource(request)
    description = normalize_description(provider.describe(), request)
    if request.get('expected_snapshot', description['snapshot_identity']) != description['snapshot_identity']:
        raise ValueError('远程仓库文件已不同于首次冻结清单，不重新选择版本或下载')
    if on_snapshot:
        on_snapshot(description)
    present = verify_existing(request, description)
    description["operation"] = "reused"
    if present != set(description["selected_files"]):
        returned = provider.download(description["selected_files"])
        if not isinstance(returned, (str, Path)) or Path(returned).resolve() != Path(request["path"]).resolve():
            raise ValueError("官方 SDK 返回的资产目录与请求不符，已保留部分文件")
        description["operation"] = "downloaded"
    after = normalize_description(provider.describe(), request)
    if after["snapshot_identity"] != description["snapshot_identity"]:
        raise ValueError("远程来源文件在准备期间变化，已保留部分资产")
    verify_existing(request, description)
    return description
