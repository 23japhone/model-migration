"""在修改资产前规范化明确的来源和资产要求。"""
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit


FIELDS = {"source", "repository", "revision", "expected_snapshot", "path", "scope", "task", "modalities",
          "required_files", "expected_files", "expected_identity", "max_workers"}
METADATA_NAMES = {"config.json", "configuration.json", "generation_config.json", "tokenizer_config.json",
                  "special_tokens_map.json", "processor_config.json", "preprocessor_config.json",
                  "video_preprocessor_config.json", "chat_template.json", "chat_template.jinja"}


def relative_file(value):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError("资产文件名必须是非空的 POSIX 相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value == ".":
        raise ValueError("资产文件名必须是规范化的相对路径")
    return value


def is_metadata(value):
    name = PurePosixPath(value).name
    return name in METADATA_NAMES or name.endswith((".safetensors.index.json", ".bin.index.json"))


def normalize_repository(value, source):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("远程来源必须提供 repository 或官方模型主页链接")
    value = value.strip()
    if "://" in value:
        value = repository_from_url(value, source)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", value) or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("repository 必须采用 namespace/name 格式")
    return value


def repository_from_url(value, source):
    parsed = urlsplit(value)
    hosts = {"huggingface": {"huggingface.co"}, "modelscope": {"modelscope.cn", "www.modelscope.cn"}}
    if parsed.scheme != "https" or parsed.hostname not in hosts[source] or parsed.username or parsed.password:
        raise ValueError("repository 链接必须指向官方 HTTPS 模型主页，且不能包含凭据")
    if parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError("请使用 repository 和 revision 字段，不要通过链接查询参数指定")
    parts = unquote(parsed.path).strip("/").split("/")
    if source == "modelscope" and parts and parts[0] == "models":
        parts = parts[1:]
    if len(parts) != 2:
        raise ValueError("请提供模型主页链接，并单独指定 revision")
    return "/".join(parts)


def expected_files(value):
    if not isinstance(value, dict):
        raise ValueError("expected_files 必须将相对文件名映射到 size 或 sha256 约束")
    result = {}
    for name, constraints in value.items():
        relative_file(name)
        if not isinstance(constraints, dict) or not constraints or constraints.keys() - {"size", "sha256"}:
            raise ValueError("预期文件约束必须包含 size 或 sha256，也可同时包含两者")
        size = constraints.get("size")
        if "size" in constraints and (type(size) is not int or size < 0):
            raise ValueError("预期文件大小必须为非负整数")
        digest = constraints.get("sha256")
        if "sha256" in constraints and (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("预期 sha256 必须为 64 位小写十六进制摘要")
        result[name] = dict(constraints)
    return result


def normalize_request(value, base=None):
    if not isinstance(value, dict) or value.keys() - FIELDS:
        raise ValueError("请求必须是仅包含契约字段的对象，不能包含凭据")
    source = value.get("source")
    scope = value.get("scope", "full")
    if source not in ("local", "huggingface", "modelscope") or scope not in ("metadata", "full"):
        raise ValueError("source 必须为 local/huggingface/modelscope；scope 必须为 metadata/full")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise ValueError("path 必须指定非空的资产目录")
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = Path(base or Path.cwd()) / root
    result = {"source": source, "path": str(root.absolute()), "scope": scope}
    normalize_source(value, result)
    normalize_asset_requirements(value, result)
    return result


def normalize_source(value, result):
    if 'expected_snapshot' in value:
        expected = value['expected_snapshot']
        if result['source'] == 'local' or not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
            raise ValueError('expected_snapshot 只接受远程文件清单的完整 SHA256')
        result['expected_snapshot'] = expected
    if result["source"] == "local":
        if {"repository", "revision"} & value.keys():
            raise ValueError("本地内容不能声明已验证的上游 repository/revision，请使用 expected_files")
        return
    result["repository"] = normalize_repository(value.get("repository"), result["source"])
    revision = value.get("revision", "main" if result["source"] == "huggingface" else "master")
    if not isinstance(revision, str) or not revision.strip() or "\0" in revision:
        raise ValueError("revision 不能为空")
    result["revision"] = revision
    workers = value.get("max_workers", 2)
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("max_workers 必须为 1 至 32 的整数")
    result["max_workers"] = workers


def normalize_asset_requirements(value, result):
    normalize_task(value, result)
    required = value.get("required_files", [])
    if not isinstance(required, list):
        raise ValueError("required_files 必须为列表")
    constraints = expected_files(value.get("expected_files", {}))
    result["required_files"] = sorted({relative_file(name) for name in required} | constraints.keys())
    if result["scope"] == "metadata" and any(
            name.endswith((".safetensors", ".bin", ".pt", ".pth", ".gguf")) for name in result["required_files"]):
        raise ValueError("metadata 范围不能请求权重文件")
    result["expected_files"] = constraints
    identity = value.get("expected_identity")
    if identity is not None:
        if not isinstance(identity, dict) or set(identity) != {"format", "sha256"} or identity["format"] != "vllm-model-file-list-v1":
            raise ValueError("expected_identity 必须包含 format=vllm-model-file-list-v1 和 sha256")
        expected_files({"identity": {"sha256": identity["sha256"]}})
        result["expected_identity"] = dict(identity)


def normalize_task(value, result):
    task = value.get("task")
    if result["scope"] == "full" and task not in ("chat", "generate", "embedding", "rerank"):
        raise ValueError("full 准备必须指定 task=chat/generate/embedding/rerank")
    if task is not None:
        if task not in ("chat", "generate", "embedding", "rerank"):
            raise ValueError("未支持的任务类型")
        result["task"] = task
    modalities = value.get("modalities", ["text"])
    if not isinstance(modalities, list) or not modalities or any(
            item not in ("text", "image", "video", "audio") for item in modalities):
        raise ValueError("modalities 必须为非空列表，仅允许 text/image/video/audio")
    result["modalities"] = sorted(set(modalities))
