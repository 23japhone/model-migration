"""只委托布局索引中的公开 CLI，并保留每次原始回执。"""
from pathlib import Path
import sys

from coordination.runtime import sources
from coordination.runtime.process import call
from skill_paths import skill_root

ENTRIES = {'vllm-migration-prepare': 'prepare.py', 'vllm-migration-execute': 'execute.py',
           'vllm-precision-align': 'precision.py', 'vllm-dataset-prepare': 'prepare.py',
           'vllm-model-migration': 'migrate.py'}


def execute(session, key, skill, request, work_root, action=None):
    source = session.artifact(key, request)
    root = session.attempt / 'children' / key
    root.mkdir(parents=True)
    args = [sys.executable, '-B', str(skill_root(skill) / 'scripts' / ENTRIES[skill]),
            '--request', source['path'], '--work-root', str(work_root), '--run-id', session.value['run_id']]
    if action:
        args.extend(('--action', action))
    session.step(key, 'running', request=source)
    code, stdout = call(args, 86400, root, 'execute')
    printed = sources.parse(stdout)
    location = printed.get('manifest') or printed.get('report_path') or printed.get('evidence', {}).get('manifest')
    if not isinstance(location, str):
        raise ValueError('公开阶段未返回持久回执')
    path = Path(location)
    if not path.is_relative_to(Path(work_root) / session.value['run_id']) or path.resolve() != path:
        raise ValueError('阶段回执不属于本次运行根')
    value = sources.read(path)
    if value.get('task_id') != skill or value.get('run_id') != session.value['run_id']:
        raise ValueError('阶段回执身份不一致')
    status = value.get('status')
    if status not in ('completed', 'failed', 'blocked'):
        raise ValueError('阶段没有保存执行终态')
    if status == 'completed' and code != 0:
        raise ValueError('阶段退出码与完成状态不一致')
    session.step(key, status, receipt=sources.ref(path), request=source, exit_code=code)
    return value
