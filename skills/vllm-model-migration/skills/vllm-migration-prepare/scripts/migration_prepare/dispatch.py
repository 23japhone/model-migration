"""仅调用固定的三个任务入口，保留各自的失败边界。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
import json
from pathlib import Path
import subprocess
import sys

from .results import EvidenceWriteError, identity, resource_inventory, safe_text, write_json
from .process import call


def command(name, spec, request, root, run_id, plan=False):
    entry = release_skill(f'vllm-{name}-prepare')/'scripts/prepare.py'
    argv = [spec.get('python', sys.executable), str(entry), '--request', str(request),
            '--work-root', str(root), '--run-id', run_id]
    return argv + (['--plan'] if plan else [])


def resources(name):
    skill = release_skill(f'vllm-{name}-prepare')
    # 指令由调用方完整阅读；摘要让后续可确认实际使用的资源副本。
    (skill/'SKILL.md').read_text(encoding='utf-8')
    schema = release_skill('vllm-model-migration')/'references/task-result.schema.json'
    schema.read_text(encoding='utf-8')
    return resource_inventory([skill, schema])


def preflight(name, spec, root, run_id):
    result = {'required': spec['required'], 'status': 'blocked', 'conclusions': {'assets': 'not_evaluated'}}
    try:
        if 'error' in spec:
            raise ValueError(spec['error'])
        result['resources'] = resources(name)
        process = call(command(name, spec, spec['request'], root, run_id, True), min(60, spec['timeout_seconds']))
        if process.returncode != 0:
            raise ValueError('任务计划无效：'+safe_text(process.stderr))
        plan = json.loads(process.stdout)
        if not isinstance(plan, dict) or plan.get('status') != 'not_run' or not isinstance(plan.get('request'), dict):
            raise ValueError('任务计划返回了无效结构')
        plan_paths(plan['request'])
        result.update(status='not_run', request=plan['request'], input_identity=identity(plan['request']))
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        result['diagnostic'] = {'type': type(error).__name__, 'message': '任务前置检查未通过：'+safe_text(error)}
    return result


def plan_paths(request):
    for field in ('path', 'archive'):
        if field in request:
            value = request[field]
            if not isinstance(value, str) or not value or '\0' in value or not Path(value).is_absolute():
                raise ValueError('子任务计划的资产路径无效：'+field)


def scope_error(name, item, purpose, target):
    request = item['request']
    if name == 'model' and purpose in ('deployment', 'deployment_precision') and request.get('scope') != 'full':
        return '部署准备必须使用 full 模型，metadata 不能替代完整权重'
    if name != 'image':
        return None
    if target is None:
        return '镜像准备必须明确阶段 target，不能仅凭子任务配方推断目标'
    recipe = request['recipe']
    if (request['platform'], request['hardware']) != (target['platform'], target['hardware']):
        return '镜像请求的平台或硬件与阶段 target 不一致'
    if (recipe['vllm'], recipe['vllm_ascend']) != (target['vllm'], target['vllm_ascend']):
        return '镜像版本配方与阶段 target 不一致'
    return None


def check_boundaries(tasks, root, run_id):
    paths = [(name, Path(item['request'][field]).resolve()) for name, item in tasks.items() if 'request' in item
             for field in ('path', 'archive') if field in item['request']]
    runtime = Path(root).resolve()/run_id
    for name, path in paths:
        conflict = path.is_relative_to(runtime) or runtime.is_relative_to(path)
        conflict |= any(other != name and (path.is_relative_to(p) or p.is_relative_to(path)) for other, p in paths)
        if conflict:
            tasks[name].update(status='blocked', diagnostic={'type': 'PathConflict',
                                'message': '资产路径与运行目录或其他资产重叠，请分开存放'})


def plan_tasks(request, root, run_id):
    tasks = {}
    for name, spec in request['tasks'].items():
        item = preflight(name, spec, root, run_id)
        if item['status'] == 'not_run':
            try:
                error = scope_error(name, item, request['purpose'], request.get('target'))
            except (KeyError, TypeError) as failure:
                error = '子任务计划缺少有效的范围或目标字段：'+safe_text(failure)
            if error:
                item.update(status='blocked', diagnostic={'type': 'ScopeConflict', 'message': error})
        tasks[name] = item
    check_boundaries(tasks, root, run_id)
    return tasks


def run_task(name, spec, item, attempt, root, run_id):
    frozen = attempt/(name+'-request.json')
    write_json(frozen, item['request'])
    argv = command(name, spec, frozen, root, run_id)
    try:
        result = call(argv, spec['timeout_seconds'])
        record = {'argv': argv, 'returncode': result.returncode, 'stdout': safe_text(result.stdout),
                  'stderr': safe_text(result.stderr)}
        write_json(attempt/(name+'-process.json'), record)
        return result
    except EvidenceWriteError:
        raise
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        write_json(attempt/(name+'-process.json'), {'argv': argv, 'diagnostic': safe_text(error),
                   'type': type(error).__name__, 'stdout': safe_text(getattr(error, 'stdout', '') or ''),
                   'stderr': safe_text(getattr(error, 'stderr', '') or ''), 'pid': getattr(error, 'pid', None)})
        raise
