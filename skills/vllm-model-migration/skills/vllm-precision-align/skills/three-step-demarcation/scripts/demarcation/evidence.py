"""不可覆盖的包外尝试、原子报告与证据文件。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

TASK_ID = 'three-step-demarcation'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 含重复字段：' + key)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限 JSON 数字')))


def load(path):
    return decode(Path(path).read_text(encoding='utf-8'))


def bound(reference):
    path = Path(reference['path'])
    if path.resolve() != path or sha256(path) != reference['sha256']:
        raise ValueError('引用文件路径或内容身份不一致：' + path.name)
    result = load(path)
    if not isinstance(result, dict):
        raise ValueError('引用内容必须为 JSON 对象')
    return result


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                             encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def record(request, run_id, action, status):
    return {'schema_version': '1.0', 'task_id': TASK_ID, 'run_id': run_id,
            'operation': action, 'inputs': request, 'status': status, 'success': False,
            'conclusions': {'demarcation': 'not_evaluated'},
            'evidence': {}, 'uncovered': [], 'detailed_report': {}, 'started_at': now()}


def begin(parent, request, run_id, action):
    attempt = parent / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    manifest = record(request, run_id, action, 'running')
    manifest['report_path'] = str(attempt / 'demarcation-manifest.json')
    write(manifest['report_path'], manifest)
    return attempt, manifest


def finish(manifest, status):
    manifest['status'] = status
    manifest['finished_at'] = now()
    conclusion = manifest['conclusions'].get('demarcation')
    manifest['success'] = status == 'completed' and conclusion == 'pass'
    if status == 'completed':
        manifest['summary'] = '定界完成；primary=' + str(manifest.get('detailed_report', {}).get('primary'))
    else:
        manifest['summary'] = '定界未完成，详见诊断及保留证据'
    write(manifest['report_path'], manifest)
    code = 0 if manifest['success'] else (3 if status == 'blocked' else 1)
    return code, manifest
