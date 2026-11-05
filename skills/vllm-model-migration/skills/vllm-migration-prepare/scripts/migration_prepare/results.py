"""包外独立尝试、中文诊断、原子台账和交接。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, resource_snapshot
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import uuid



class EvidenceWriteError(OSError):
    """共享证据基础失效，停止后续操作。"""


def safe_text(value):
    value = str(value)
    value = re.sub(r'(?i)((?:proxy-)?authorization\s*[\"\x27]*\s*[:=]\s*[\"\x27]*)(?:bearer|basic)\s+[^\s,;\"\x27]+', r'\1<已脱敏>', value)
    value = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1<已脱敏>@', value)
    value = re.sub(r'(?i)(https?://[^\s?#]+)[?#][^\s]*', r'\1?<已脱敏>', value)
    value = re.sub(r'(?i)(bearer|basic)\s+[^\s,;\"\x27]+', '<已脱敏>', value)
    return re.sub(r'(?i)(password|token|secret)([\s\"\x27]*[:=][\s\"\x27]*)([^\s,;\"\x27]+)', r'\1\2<已脱敏>', value)


def sanitized(value):
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, dict):
        secrets = {'token', 'password', 'secret', 'authorization', 'proxy-authorization', 'api_key'}
        return {key: '<已脱敏>' if key.lower() in secrets else sanitized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return value


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resource_inventory(paths):
    files = []
    for path in paths:
        files.extend(owned_files(path))
    return resource_snapshot(files)


def write_json(path, value):
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.phase-evidence-', dir=path.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    except OSError as error:
        raise EvidenceWriteError('无法保存阶段证据：'+safe_text(error)) from error
    finally:
        if temporary is not None and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError as error:
                raise EvidenceWriteError('无法清理阶段临时证据：'+safe_text(error)) from error


def protected_root():
    return release_root()


def output_parent(work_root, run_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id) or run_id in ('.', '..'):
        raise ValueError('run_id 必须为安全的单层运行标识')
    parent = Path(work_root).expanduser().resolve()/run_id/'vllm-migration-prepare'
    protected = protected_root()
    if parent.resolve() != parent or parent.is_relative_to(protected) or protected.is_relative_to(parent):
        raise ValueError('阶段证据必须位于技能包外，运行目录不能通过符号链接重定向')
    return parent


def begin_attempt(parent, request, run_id):
    parent.mkdir(parents=True, exist_ok=True)
    attempt = parent/uuid.uuid4().hex
    attempt.mkdir()
    manifest = {'schema_version': '1.0', 'run_id': run_id, 'task_id': 'vllm-migration-prepare',
                'inputs': sanitized(request), 'input_identity': identity(request), 'status': 'running', 'tasks': {},
                'resources': resource_inventory([release_skill('vllm-migration-prepare'), release_root()]),
                'host': socket.gethostname(), 'started_at': datetime.now(timezone.utc).isoformat(),
                'conclusions': {'assets': 'not_evaluated', 'version_materials': 'not_evaluated',
                                'preparation': 'not_evaluated', 'runtime_support': 'not_evaluated'},
                'evidence': {'manifest': str(attempt/'prepare-manifest.json'), 'ledger': str(attempt/'task-ledger.json'),
                             'handoff': str(attempt/'prepare-handoff.json')},
                'uncovered': ['未加载模型、启动服务、验证实际 NPU 运行或执行精度评分']}
    return attempt, manifest


def save(attempt, manifest):
    write_json(attempt/'task-ledger.json', sanitized(manifest['tasks']))
    write_json(attempt/'prepare-manifest.json', sanitized(manifest))


def finish(attempt, manifest):
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    manifest['status'] = phase_status(manifest)
    code = {'completed': 0, 'failed': 1, 'blocked': 3}[manifest['status']]
    write_json(attempt/'prepare-handoff.json', sanitized({
        'schema_version': '1.0', 'run_id': manifest['run_id'], 'phase_manifest': manifest['evidence']['manifest'],
        'target': manifest['inputs'].get('target'), 'conclusions': manifest['conclusions'],
        'assets': {name: {'required': item['required'], 'status': item['status'], 'manifest': item.get('manifest'),
                          'manifest_sha256': item.get('manifest_sha256'), 'asset': item.get('asset'),
                          'conclusions': item.get('conclusions', {})} for name, item in manifest['tasks'].items()},
        'version_review': manifest.get('version_review', []), 'uncovered': manifest['uncovered']}))
    # 最后发布终态，交接写入失败时旧清单仍保持未完成状态。
    save(attempt, manifest)
    return code, {'manifest': manifest['evidence']['manifest'], 'status': manifest['status'], 'conclusions': manifest['conclusions']}


def phase_status(manifest):
    required = [t for t in manifest['tasks'].values() if t['required']]
    failed = any(t['status'] == 'failed' for t in required)
    ready = bool(required) and all(t['status'] == 'completed' and t['conclusions'].get('assets') == 'pass' for t in required)
    manifest['conclusions']['assets'] = 'pass' if ready else ('fail' if failed else 'not_evaluated')
    versions = manifest['conclusions']['version_materials']
    if failed or versions == 'fail' or manifest.get('cancelled'):
        manifest['conclusions']['preparation'] = 'fail'
        return 'failed'
    if not ready or versions == 'review_required':
        manifest['conclusions']['preparation'] = 'review_required'
        return 'blocked'
    manifest['conclusions']['preparation'] = 'pass'
    return 'completed'
