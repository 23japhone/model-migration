"""客户流程对新增准备/部署公开操作的固定委托。"""
import sys
import uuid

from coordination.runtime import sources
from coordination.runtime.process import call
from skill_paths import skill_root
from .evaluation import lifecycle


class OperationBlocked(ValueError):
    def __init__(self, report):
        super().__init__(report.get('reason', '公开准备操作未完成'))
        self.report = report


def operation(session, key, owner, request, work_root, python=None, entry='customer.py', flag='--request'):
    source = session.artifact(key, request)
    folder = session.attempt / 'operations' / key / uuid.uuid4().hex
    folder.mkdir(parents=True)
    argv = [python or sys.executable, '-B', str(skill_root(owner) / 'scripts' / entry), flag, source['path']]
    if entry != 'resolve.py':
        argv.extend(['--work-root', str(work_root), '--run-id', session.value['run_id']])
    code, output = call(argv, 7200, folder, key)
    printed = sources.parse(output)
    status = printed.get('status')
    preview = (request.get('operation') == 'evalscope_environment'
               and any(request.get('install', {}).get(key) is True for key in ('help', 'dry_run')))
    if code or status not in (('not_run',) if preview else ('completed', 'resolved')):
        session.step(key, 'failed' if status == 'failed' else 'blocked', request=source,
                     receipt=session.artifact(key + '-result', printed), diagnostic=printed.get('diagnostic'))
        raise OperationBlocked(printed)
    reference = session.artifact(key + '-result', printed)
    session.step(key, 'not_run' if preview else 'completed', receipt=reference, request=source)
    return printed.get('result', printed)


def evaluator(session, task, work_root, install=None):
    if task['accuracy']['evaluator'] == 'evalscope':
        return lifecycle.run(session, work_root, install, operation)
    if install:
        raise ValueError('evalscope_install 不适用于 AISBench')
    old = session.ready('environment')
    request = {'operation': 'environment', 'packages': ['ais-bench-benchmark', 'huggingface-hub', 'modelscope', 'transformers']}
    if old:
        request['versions'] = old['result']['versions']
    return operation(session, 'environment', 'vllm-migration-prepare',
                     request, work_root)


def preview(session, task, work_root, install):
    result = evaluator(session, task, work_root, install)
    if result.get('status') != 'not_run':
        raise ValueError('安装帮助或预览不得产生就绪结论')
    return session.finish('not_run', next_action='install_preview_only', installation=result)


def resolve_model(session, task, environment, work_root):
    old = session.ready('model_resolution')
    if old:
        return old
    try:
        return operation(session, 'model_resolution', 'vllm-model-prepare', task['model'], work_root,
                         python=environment.get('python', sys.executable), entry='resolve.py', flag='--model')
    except OperationBlocked as error:
        package = error.report.get('missing_dependency')
        if package not in ('modelscope', 'huggingface-hub', 'modelscope-hub'):
            raise
        isolated = operation(session, 'asset_environment', 'vllm-migration-prepare',
                             {'operation': 'environment', 'packages': [package]}, work_root)
        result = operation(session, 'model_resolution', 'vllm-model-prepare', task['model'], work_root,
                           python=isolated['python'], entry='resolve.py', flag='--model')
        return {**result, 'asset_python': isolated['python']}


def functional_inputs(session, task, asset, observations, jobs, work_root):
    """公开原生模板观察先于设备与容器操作，失败不产生部署效果。"""
    from .compile import generation
    recipe = observations['deployment']
    request = generation.observation_request(task, asset, recipe)
    observed = operation(session, 'functional_inputs', 'verify-agent', request, work_root,
                         python=observations['evaluator']['python'])
    functional = generation.bind(observed, request, recipe)
    return functional, generation.deployment_context(jobs, functional, recipe)


def precheck_plan(session, asset, observations, work_root):
    """在功能观察和资源效果前验证完整预检请求；计划通过不代表静态支持通过。"""
    from .compile import deployment
    folder = session.attempt / 'operations' / 'precheck_plan' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    session.step('precheck_plan', 'running', request=None, source_projection=None, receipt=None, diagnostic=None)
    try:
        original = session.artifact('precheck-source-observation', observations['deployment']['sources'])
        projection = session.artifact('precheck-sources', {
            'source': original, **deployment.source_projection(observations['deployment']['sources'])})
        request = session.artifact('precheck', deployment.precheck(asset, observations))
        session.step('precheck_plan', 'running', request=request, source_projection=projection)
        argv = [sys.executable, '-B', str(skill_root('vllm-model-precheck') / 'scripts' / 'precheck.py'),
                '--request', request['path'], '--work-root', str(work_root),
                '--run-id', session.value['run_id'], '--plan']
        code, output = call(argv, 60, folder, 'precheck-plan')
        if code:
            diagnostic = (folder / 'precheck-plan.stderr.txt').read_text(encoding='utf-8')
            raise ValueError('预检请求计划校验失败：' + diagnostic.strip()[:4096])
        result = sources.parse(output)
        if result.get('status') != 'not_run' or not isinstance(result.get('request'), dict):
            raise ValueError('预检计划未返回有效 not_run 请求')
        receipt = session.artifact('precheck-plan-result', result)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as error:
        session.step('precheck_plan', 'failed' if isinstance(error, KeyboardInterrupt) else 'blocked',
                     diagnostic={'type': type(error).__name__, 'message': str(error)})
        raise
    session.step('precheck_plan', 'not_run', request=request, source_projection=projection, receipt=receipt, diagnostic=None)
    return request
