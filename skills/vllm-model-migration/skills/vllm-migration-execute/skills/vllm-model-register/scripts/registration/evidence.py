"""稳定资料、脱敏与包外回执；不执行目标代码。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

SECRET_KEYS = {'password', 'token', 'access_token', 'api_key', 'authorization', 'secret', 'credentials', 'hf_token'}


def safe_text(value):
    text = str(value)
    for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'MODELSCOPE_API_TOKEN'):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, '<已脱敏>')
    text = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1<已脱敏>@', text)
    text = re.sub(r'(?i)(https?://[^\s?#]+)[?#][^\s]*', r'\1?<已脱敏>', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)[^\r\n]+', r'\1<已脱敏>', text)
    return re.sub(r'(?i)(password|token|secret|api_key)(\s*[:=]\s*)[^\s,;]+', r'\1\2<已脱敏>', text)


def sanitized(value):
    if isinstance(value, dict):
        return {key: '<已脱敏>' if key.lower() in SECRET_KEYS else sanitized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return safe_text(value) if isinstance(value, str) else value


def read_bytes(path, limit=4 * 1024 * 1024):
    path = Path(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = path.stat()
    stamps = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in (before, after, current)]
    if len(raw) > limit or stamps[0] != stamps[1] or stamps[1] != stamps[2]:
        raise ValueError('资料过大或读取期间发生变化：' + path.name)
    return raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 重复字段：' + key)
        result[key] = value
    return result


def read_json(path):
    raw = read_bytes(path)
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_pairs)
    if not isinstance(value, dict):
        raise ValueError('JSON 根必须为对象')
    return value, digest(raw)


def write_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.register-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(sanitized(value), stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def now():
    return datetime.now(timezone.utc).isoformat()


def begin_attempt(target, request, run_id, operation):
    if target.resolve() != target:
        raise ValueError('运行目录不能经符号链接重定向')
    attempt = target / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': '1.0', 'task_id': 'vllm-model-register', 'run_id': run_id,
                'skill_version': '0.2.0-dev.1', 'operation': operation, 'inputs': request,
                'status': 'running', 'conclusions': {'registration': 'not_evaluated',
                    'weight_loading': 'not_evaluated', 'functional_validation': 'not_evaluated'},
                'evidence': {'manifest': str(attempt / 'registration-manifest.json')},
                'uncovered': ['注册与 class import 不证明真实权重、NPU 推理或服务功能'], 'started_at': now()}
    write_json(attempt / 'request.json', request)
    write_json(attempt / 'registration-manifest.json', manifest)
    return attempt, manifest
