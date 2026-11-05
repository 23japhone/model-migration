"""保存包外证据、独立尝试及脱敏诊断。"""

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


def safe_text(value):
    value = str(value)
    value = re.sub(r'(?i)(authorization\s*[:=]\s*)[\"\x27]?(?:bearer|basic)\s+[^\s,;\"\x27]+', r'\1<已脱敏>', value)
    value = re.sub(r'(https?://)[^/\s@]+@', r'\1<已脱敏>@', value, flags=re.I)
    value = re.sub(r'(https?://[^\s?#]+)[?#][^\s]*', r'\1?<已脱敏>', value)
    value = re.sub(r'(?i)(bearer\s+)[^\s,;]+', r'\1<已脱敏>', value)
    return re.sub(r'(?i)(password|token|secret|authorization)([\s\"\x27]*[:=][\s\"\x27]*)([^\s,;\"\x27]+)', r'\1\2<已脱敏>', value)


def write_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.image-evidence-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def output_parent(work_root, run_id, request):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id) or run_id in ('.', '..'):
        raise ValueError('run_id 必须是安全的单层运行标识')
    root = Path(work_root).expanduser().resolve()
    protected = release_root()
    target = root/run_id/'vllm-image-prepare'
    if target.resolve() != target:
        raise ValueError('运行输出目录不能通过符号链接重定向')
    if target.is_relative_to(protected) or protected.is_relative_to(target):
        raise ValueError('证据必须保存在技能资源包外')
    if request.get('archive') and (target == Path(request['archive']) or target.is_relative_to(Path(request['archive']))):
        raise ValueError('证据目录不能覆盖镜像归档')
    return target


def begin_attempt(parent, request, run_id):
    parent.mkdir(parents=True, exist_ok=True)
    attempt = parent / uuid.uuid4().hex
    attempt.mkdir()
    manifest = {'schema_version': '1.0', 'run_id': run_id, 'task_id': 'vllm-image-prepare',
                'inputs': request, 'status': 'running',
                'conclusions': {'assets': 'not_evaluated', 'runtime_support': 'not_evaluated',
                                'hardware_compatibility': 'review_required', 'source': 'not_evaluated'},
                'evidence': {'manifest': str(attempt/'image-manifest.json'), 'commands': str(attempt/'docker-commands.json')},
                'uncovered': ['未启动容器或验证 vLLM-Ascend、CANN、驱动与目标 NPU 的实际运行',
                              '硬件名称、系列和版本配方来自输入；需结合官方资料和目标环境复核'],
                'started_at': datetime.now(timezone.utc).isoformat(), 'host': socket.gethostname()}
    return attempt, manifest
