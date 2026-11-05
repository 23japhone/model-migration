"""正式环境准备与预览分离；安装前后保护实际绑定。"""
from evalscope_installation import STRINGS
from coordination.runtime.evidence import EvidenceError
from . import probe


def invalidate(session, reason):
    session.value['evalscope_environment'] = {**session.value['evalscope_environment'], 'invalidated': True}
    session.save()
    raise ValueError(reason + '，必须使用新 run-id；旧精度结果不可复用')


def bind_environment(session, result):
    binding = {'python': result['python'], 'version': result['versions']['evalscope']}
    old = session.value.get('evalscope_environment')
    if old is not None and old != binding:
        invalidate(session, '实际 Python 或 EvalScope 版本变化')
    session.value['evalscope_environment'] = binding
    session.save()


def remember(session, plan, request):
    try:
        selected = plan.selection or {
            'options': {name: plan.options[name] for name in STRINGS if name in plan.options},
            'source': session.artifact('evalscope-install-selection', request)}
    except (OSError, ValueError) as error:
        raise EvidenceError('安装选择证据不可写或已损坏') from error
    session.value['evalscope_install_selection'] = selected
    session.save()


def failed_installation(session, options, binding, original):
    try:
        probe.check(session, options, binding)
    except EvidenceError:
        raise
    except probe.Changed as error:
        invalidate(session, '安装失败后确认环境变化：' + str(error) + '；原失败：' + str(original))
    except (OSError, ValueError, RuntimeError) as error:
        session.step('environment_post_failure_check', 'blocked',
                     diagnostic={'message': str(error), 'installation_error': str(original)})
    else:
        session.step('environment_post_failure_check', 'completed', value={'binding_unchanged': True})


def attempt(session, work_root, request, operation):
    try:
        return operation(session, 'environment', 'vllm-migration-prepare', request, work_root)
    except EvidenceError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        binding = session.value.get('evalscope_environment')
        if binding:
            failed_installation(session, request['install'], binding, error)
        raise


def run(session, work_root, plan, operation):
    request = {'operation': 'evalscope_environment', 'install': plan.options}
    if plan.is_preview:
        if not plan.options:
            raise ValueError(plan.problem)
        result = operation(session, 'environment_preview', 'vllm-migration-prepare', request, work_root)
        if result.get('status') != 'not_run':
            raise ValueError('安装帮助或预览不得产生就绪结论')
        return {**result, **({'notice': plan.problem} if plan.problem else {})}
    if plan.problem:
        raise ValueError(plan.problem)
    binding = session.value.get('evalscope_environment')
    if binding:
        try:
            probe.check(session, plan.options, binding)
        except probe.Changed as error:
            invalidate(session, str(error))
    remember(session, plan, request)
    result = attempt(session, work_root, request, operation)
    bind_environment(session, result)
    return result
