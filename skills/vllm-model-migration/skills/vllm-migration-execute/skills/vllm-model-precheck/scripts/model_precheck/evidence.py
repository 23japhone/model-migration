"""稳定读取资料、建立内容身份并原子保存包外证据。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


SECRET_KEYS = {'password', 'token', 'access_token', 'api_key', 'authorization',
               'proxy-authorization', 'secret', 'credentials', 'hf_token'}


def safe_text(value):
    text = str(value)
    for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'MODELSCOPE_API_TOKEN'):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, '<已脱敏>')
    text = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1<已脱敏>@', text)
    text = re.sub(r'(?i)(https?://[^\s?#]+)[?#][^\s]*', r'\1?<已脱敏>', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)[^\r\n]+', r'\1<已脱敏>', text)
    return re.sub(r'(?i)(password|token|secret|api_key)(\s*[:=]\s*)[^\s,;]+',
                  r'\1\2<已脱敏>', text)


def sanitized(value):
    if isinstance(value, dict):
        return {key: '<已脱敏>' if key.lower() in SECRET_KEYS else sanitized(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return safe_text(value) if isinstance(value, str) else value


def identity(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def read_bytes(path, limit=4 * 1024 * 1024):
    path = Path(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > limit:
            raise ValueError('资料超过允许大小：' + path.name)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = path.stat()
    stamps = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
              for s in (before, after, current)]
    if len(raw) > limit or stamps[0] != stamps[1] or stamps[1] != stamps[2]:
        raise ValueError('读取期间资料发生变化：' + path.name)
    return raw


def read_json(path):
    raw = read_bytes(path)
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_pairs)
    if not isinstance(value, dict):
        raise ValueError('JSON 根必须为对象：' + Path(path).name)
    return value, hashlib.sha256(raw).hexdigest()


def unique_pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError('JSON 存在重复字段：' + key)
        output[key] = value
    return output


def file_record(path):
    raw = read_bytes(path)
    return {'path': str(Path(path)), 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def write_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.precheck-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(sanitized(value), stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def begin_attempt(target, request, run_id):
    # 创建前再次检查已有父路径，拒绝符号链接重定向。
    if target.resolve() != target:
        raise ValueError('运行目录不能通过符号链接重定向')
    attempt = target / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': '1.0', 'run_id': run_id, 'task_id': 'vllm-model-precheck',
                'skill_version': '0.2.0-dev.1', 'inputs': request, 'input_identity': identity(request),
                'status': 'running', 'conclusions': {'support': 'not_evaluated',
                    'version_materials': 'not_evaluated', 'runtime_support': 'not_evaluated'},
                'evidence': {'manifest': str(attempt / 'precheck-manifest.json')},
                'uncovered': ['静态预检未证明权重加载、实际注册导入、NPU 推理或服务功能'],
                'started_at': datetime.now(timezone.utc).isoformat()}
    write_json(attempt / 'request.json', request)
    write_json(attempt / 'precheck-manifest.json', manifest)
    return attempt, manifest
