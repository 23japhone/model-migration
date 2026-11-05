"""仅通过固定公开 CLI 调用阶段，固定计划、输入及调用身份。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[3] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
import sys
import uuid

from ..planning import STAGES
from . import receipts, sources
from .evidence import write
from .process import call
from .preflight import Blocked


def delegate(key, request, attempt, work_root, run_id, action, timeout, protected):
    skill, script = STAGES[key]
    entry = release_skill(skill)/'scripts'/script
    if not entry.is_file():
        raise Blocked('缺少公开阶段入口：'+skill)
    implementation = sources.ref(entry)
    child_run = run_id[:40]+'-'+uuid.uuid4().hex[:16]
    root = work_root/child_run/skill
    sources.protect(root, protected)
    if root.exists():
        raise ValueError('阶段输出已存在，不能复用运行目录')
    path = attempt/(key+'.request.json')
    write(path, request)
    argv = [sys.executable, '-B', str(entry), '--request', str(path), '--work-root', str(work_root), '--run-id', child_run]
    if key not in ('preparation', 'dataset_preparation'):
        argv.extend(['--action', action if key == 'precision' else 'run'])
    receipt_key = 'preparation' if key == 'dataset_preparation' else key
    code, text = call([*argv, '--plan'], min(timeout, 60), attempt, key+'-plan')
    plan = sources.parse(text) if text.strip() else {}
    # 准备计划中的分项阻塞不抹掉可独立完成的其他准备任务。
    allowed = (0, 2) if receipt_key == 'preparation' and isinstance(plan.get('tasks'), dict) else (0,)
    if code not in allowed or plan.get('status') != 'not_run':
        raise Blocked('阶段计划拒绝输入，查看 plan 证据')
    receipts.stage_plan(plan, receipt_key, request, child_run, action)
    if key == 'migration':
        write(attempt/'migration.invocation.json', {'run_id':child_run, 'root':str(root),
              'request':request, 'request_source':sources.ref(path)})
    code, text = call(argv, timeout, attempt, key)
    value, source = receipts.receipt(text, code, receipt_key, skill, request, root, child_run, action, plan)
    if sources.ref(entry) != implementation:
        raise ValueError('阶段入口在执行期间变化')
    write(attempt/(key+'.implementation.json'), {'entry': implementation,
          'reported_version': value.get('skill_version'), 'schema_version': value['schema_version']})
    return value, source, code
