"""包外运行索引、内容摘要和原子证据写入。"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def safe_text(value):
    text = str(value)
    text = re.sub(r'(?i)(Bearer|Basic)\s+\S+', r'\1 [已隐藏]', text)
    return re.sub(r'(?i)((?:token|password|api_key)\s*[=:]\s*)[^\s,]+', r'\1[已隐藏]', text)


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_bound(item):
    path = Path(item['path'])
    if path.is_symlink() or sha256(path) != item['sha256']:
        raise ValueError('交接文件身份不一致')
    return json.loads(path.read_text(encoding='utf-8'))


def begin(target, request, run_id, operation):
    attempt = target / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': '1.0', 'task_id': 'verify-agent', 'run_id': run_id,
                'operation': operation, 'status': 'running', 'success': False,
                'summary': '验证进行中', 'inputs': json.loads(json.dumps(request)),
                'conclusions': {}, 'evidence': {}, 'uncovered': [], 'detailed_report': {},
                'started_at': datetime.now(timezone.utc).isoformat(),
                'report_path': str(attempt / 'verification-manifest.json')}
    write_json(manifest['report_path'], manifest)
    return attempt, manifest


def finish(manifest, status):
    manifest['status'] = status
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    manifest['success'] = (status == 'completed' and bool(manifest['conclusions'])
                           and all(value == 'pass' for value in manifest['conclusions'].values()))
    manifest['summary'] = '本次必需检查通过' if manifest['success'] else '未满足本次全部必需检查，详见分项证据'
    write_json(manifest['report_path'], manifest)
    code = 0 if manifest['success'] else (3 if status == 'blocked' else 1)
    return code, {key: manifest[key] for key in ('success', 'summary', 'detailed_report', 'report_path',
                                               'status', 'conclusions')}
