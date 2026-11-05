"""管理本次尝试的证据目录，原子写入证据并脱敏。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import uuid


TASK_ID = "vllm-model-prepare"
SECRET_FIELDS = {"password", "token", "access_token", "api_key", "authorization", "secret", "credentials",
                 "hf_token", "ms_token", "modelscope_token", "hugging_face_hub_token"}


def safe_text(value):
    value = str(value)
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "MODELSCOPE_API_TOKEN", "MODELSCOPE_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            value = value.replace(secret, "<redacted>")
    value = re.sub(r"(?i)\b(authorization\s*[:=]\s*)[\"']?(?:bearer|basic)\s+[^\s,;]+", r"\1<redacted>", value)
    value = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1<redacted>@", value)
    value = re.sub(r"(?i)(https?://[^\s?]+)\?[^\s]+", r"\1?<redacted>", value)
    value = re.sub(r"(?i)\b(password|token|api[_-]?key|authorization|signature|credential)(\s*[:=]\s*)[^\s,;]+",
                   r"\1\2<redacted>", value)
    return value[:4000]


def sanitize(value):
    if isinstance(value, dict):
        return {key: "<redacted>" if key.lower() in SECRET_FIELDS else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return safe_text(value) if isinstance(value, str) else value


def validate_output(request, work_root, run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("run_id 必须是单一标识，不能包含路径分隔符")
    root = Path(work_root).expanduser().resolve()
    assets = Path(request["path"]).resolve()
    if root == assets or assets in root.parents:
        raise ValueError("work-root 必须位于模型目录之外")
    protected = release_root()
    if root == protected or protected in root.parents:
        raise ValueError("work-root 必须位于已安装的技能资源之外")
    if request["source"] != "local" and assets.is_relative_to(protected):
        raise ValueError("模型资产必须位于插件包外")
    target = root / run_id / TASK_ID
    if target == assets or assets in target.parents:
        raise ValueError("运行输出必须位于模型目录之外")
    if target == protected or protected in target.parents or protected.is_relative_to(target):
        raise ValueError("运行输出必须位于已安装的技能资源之外")
    if target.resolve() != target:
        raise ValueError("运行输出目录不能通过符号链接重定向")
    return target


def begin_attempt(target, request, run_id):
    attempt = target / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": "1.0", "run_id": run_id, "task_id": TASK_ID,
                "skill_version": "0.1.0-dev.1", "inputs": request,
                "status": "running", "conclusions": {"assets": "not_evaluated", "runtime_support": "not_evaluated"},
                "environment": {"hostname": socket.gethostname(), "asset_path": request["path"]},
                "started_at": datetime.now(timezone.utc).isoformat(),
                "evidence": {"manifest": str(attempt / "model-manifest.json")},
                "uncovered": ["未评估 vLLM/vLLM-Ascend 的模型支持度或 NPU 推理"]}
    return attempt, manifest


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".model-evidence-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(sanitize(value), stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
