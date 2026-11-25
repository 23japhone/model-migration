"""客户流程：独立准备继续，缺语义资料由 Agent 续接，不伪造成功。"""
import copy
from pathlib import Path
import signal

from coordination.runtime import sources
from coordination.runtime.evidence import EvidenceError, defer_interrupts
from .token_alignment import deployment as token_deployment
from . import context, effects, recovery, model_identity
from .compile import assets, accuracy, deployment
from .quality.stage import compare_all
from .delivery import deliver
from .invoke import execute
from .defaults import model as default_model, devices as default_devices, state as default_state
from .defaults import image as default_image
from .defaults.policy import pending
from .evaluation.datasets import bind_prepared, reusable_request

ERRORS = (ValueError, KeyError, TypeError, OSError, RuntimeError)


def guarded(session, key, action):
    try:
        return action()
    except EvidenceError:
        raise
    except recovery.DecisionRequired as error:
        session.step(key, 'blocked', decision=error.decision, diagnostic=None)
        return None
    except ERRORS as error:
        reason = ('缺少 Agent 观察：' + str(error)) if isinstance(error, KeyError) else str(error)
        session.step(key, 'blocked', decision=None, diagnostic={'type': type(error).__name__, 'message': reason})
        return None


def prepare(session, task, observations, work_root):
    review = model_identity.verify(task, observations)
    session.step('model_identity', 'completed', value=review)
    old = session.ready('preparation')
    if old:
        # 冻结准备回执；后续公开部署/精度入口重新核验实际文件和运行身份。
        recovery.asset_files(deployment.prepared_model(old), model=True)
        return old
    request = assets.preparation(task, observations, session)
    return execute(session, 'preparation', 'vllm-migration-prepare', request, work_root)


def data_jobs(session, task, observations, work_root):
    jobs = []
    for index, dataset in enumerate(task['accuracy']['datasets']):
        partial = copy.deepcopy(task)
        partial['accuracy']['datasets'] = [dataset]
        rows = guarded(session, 'catalog-' + str(index), lambda: assets.dataset_jobs(partial, observations))
        if rows is None:
            jobs.append({'id': 'dataset-' + str(index) + '-unresolved', 'dataset': dataset,
                         'blocked_reason': '原生数据集目录或官方配置未就绪'})
        for job in rows or []:
            job['id'] = 'dataset-' + str(index) + '-' + job['id'].rsplit('-', 1)[1]
            value = guarded(session, job['id'], lambda job=job: prepare_dataset(session, job, work_root))
            if value and value.get('status') == 'completed':
                checked = guarded(session, job['id'] + '-identity', lambda: bind_prepared(
                    session, task, job, value, observations, work_root))
                if checked:
                    job.update(checked)
            if 'prepared' not in job:
                job['blocked_reason'] = '数据准备或正式原生目录预检未通过，详见对应客户台账'
            jobs.append(job)
    return jobs


def prepare_dataset(session, job, work_root):
    old = session.ready(job['id'])
    if old:
        declared = sources.bound(session.value['steps'][job['id']]['request'])
        if not reusable_request(declared, job['request']):
            old = None
    value = old
    if value is None:
        value = execute(session, job['id'], 'vllm-dataset-prepare', job['request'], work_root)
    if job.get('native_data_files'):
        assets.catalog_files(job['dataset']['path'], job['native_data_files'])
    return value


def migrate(session, task, observations, jobs, work_root, supplied):
    if supplied.get('migration_result'):
        return recovery.migration(session, supplied['migration_result'])
    old = session.ready('migration')
    if old:
        recovery.freeze(session, 'deployment', observations['deployment'])
        return old
    prior = session.value['steps'].get('migration', {}).get('receipt')
    if prior and sources.bound(prior).get('next_action') == 'semantic_review_required':
        return sources.bound(prior)
    prepared = session.ready('preparation')
    if not prepared:
        raise ValueError('部署等待模型及镜像准备完成')
    recipe = observations['deployment']
    asset = deployment.prepared_model(prepared)
    check = effects.precheck_plan(session, asset, observations, work_root)
    functional, context_length = effects.functional_inputs(session, task, asset, observations, jobs, work_root)
    spec = deployment.capacity_spec(recipe, asset, context_length)
    host = effects.operation(session, 'host_observation', 'verify-agent',
                             {'operation': 'observe', 'capacity': True, 'host': deployment.HOST, 'port': deployment.START_PORT,
                              'container_name': task['environment']['container_name']}, work_root)
    default_devices.check_host(session, host)
    allocation = recovery.allocate(session, {**deployment.allocation_request(task, host, spec),
                                            'observation': session.value['steps']['host_observation']['receipt']}, work_root)
    recovery.container(session, task['environment'], host, supplied.get('decisions', {}), work_root)
    verification = deployment.verify(task, asset, prepared, observations, allocation, host['port'], context_length, functional)
    token_deployment.attach(session, task, verification)
    verify_ref = session.artifact('verify', verification)
    request = {'schema_version': '1.0', 'preparation': session.value['steps']['preparation']['receipt'],
               'precheck': check, 'verify': verify_ref, **deployment.extensions(recipe)}
    session.value['resolved_deployment'] = {'endpoint': verification['service']['endpoint'],
                                           'capacity': {'max_model_len': context_length,
                                                        'max_num_seqs': recipe['max_num_seqs'],
                                                        'kv_token_capacity': allocation['kv_token_capacity'],
                                                        'runtime_metrics_available': recipe['runtime_metrics_available']}}
    session.save()
    value = execute(session, 'migration', 'vllm-migration-execute', request, work_root)
    recovery.migration_capacity(value)
    if value.get('deployment'):
        recovery.freeze(session, 'deployment', recipe)
    return value


def precision(session, task, observations, jobs, work_root, reusable):
    migration = session.ready('migration')
    if not migration or not jobs:
        raise ValueError('精度等待有效部署与数据集声明')
    handoff = sources.bound(migration['deployment'])
    service_source = migration.get('review_revalidated', migration['tasks']['verify']['receipt'])
    rows, bindings = [], {}
    for job in jobs:
        if 'receipt' not in job:
            rows.append({'id': job['id'], 'blocked_reason': job.get('blocked_reason', '对应数据准备未完成')})
            bindings[job['id']] = {'deployment': 'migration'}
            continue
        value = guarded(session, job['id'] + '-configuration', lambda: evaluation_request(
            session, task, job, observations, handoff['endpoint']))
        if value is None:
            rows.append({'id': job['id'], 'blocked_reason': '精度配置生成失败，详见客户台账'})
            bindings[job['id']] = {'deployment': 'migration'}
            continue
        row = {'id': job['id'], 'request': session.artifact(job['id'] + '-evaluation', value)}
        source = reusable.get(job['id'])
        if source and sources.bound(source)['inputs'] == {**value, 'deployment': service_source}:
            row['result'] = source
        rows.append(row)
        bindings[job['id']] = {'deployment': 'migration'}
    request = {'schema_version': '1.0', 'scope': 'deployment_precision', 'stages': {
        'migration': {'result': session.value['steps']['migration']['receipt']},
        'precision': {'request': session.artifact('precision', {'schema_version': '1.0', 'evaluations': rows}),
                      'bindings': bindings}}}
    return execute(session, 'entry', 'vllm-model-migration', request, work_root)


def evaluation_request(session, task, job, observations, endpoint):
    if accuracy.context_tokens([job]) > session.value['resolved_deployment']['capacity']['max_model_len']:
        raise ValueError('实际部署上下文不足以容纳该精度项与 8192 输出预算，不截断输入')
    return accuracy.request(task, job, job['prepared'], observations['evaluator'], endpoint,
                            session, observations['deployment']['thinking_supported'])


def complete(session, task, observations, supplied, work_root, jobs):
    if (pending(task) or not all(session.ready(name) for name in ('environment', 'preparation', 'migration', 'entry'))
            or not jobs or any('receipt' not in job for job in jobs)):
        return session.finish('blocked', next_action=recovery.pending(session) or context.next_actions(['resolve_failed_steps']))
    if task['accuracy']['evaluator'] != 'evalscope':
        return session.finish('blocked', delivery_supported=False, next_action='aisbench_three_json_unsupported',
                              uncovered=['AISBench 精度可执行，本轮不交付三个 JSON'])
    runtime = supplied.get('runtime_environment')
    if not runtime:
        migration = session.ready('migration')
        runtime = effects.operation(session, 'runtime_paths', 'verify-agent',
                                    {'operation': 'runtime_paths', 'deployment': migration['deployment']}, work_root)
    result = deliver(session, session.value['steps']['entry']['receipt'],
                     session.value['resolved_deployment']['capacity'], runtime)
    return session.finish('completed', deliverables=result['deliverables'], export_manifest=result['manifest'])


def discover(session, task, observations, work_root, install=None):
    """环境、模型与缺省镜像各记台账，单项失败不阻止其他发现。"""
    observations.pop('evaluator', None)
    environment = guarded(session, 'environment', lambda: effects.evaluator(
        session, task, work_root, install))
    if environment:
        package = 'evalscope' if task['accuracy']['evaluator'] == 'evalscope' else 'ais-bench-benchmark'
        observations['evaluator'] = {'python': environment['python'], 'version': environment['versions'][package]}
        if task['accuracy']['evaluator'] == 'aisbench':
            # AISBench 使用任务自建环境，其已冻结的 SDK 可供资产准备复用。
            observations.setdefault('asset_python', environment['python'])
    if 'model_resolution' not in observations:
        asset_environment = {'python': observations['asset_python']} if observations.get('asset_python') else {}
        resolved = guarded(session, 'model_resolution', lambda: effects.resolve_model(session, task, asset_environment, work_root))
        if resolved:
            observations['model_resolution'] = resolved
    if observations.get('model_resolution', {}).get('asset_python'):
        observations['asset_python'] = observations['model_resolution']['asset_python']
    guarded(session, default_image.STEP, lambda: default_image.discover(session, task, observations, work_root))


def workflow(session, task, supplied, work_root, install):
    if install is not None and install.is_preview:
        return effects.preview(session, task, work_root, install)
    observations = copy.deepcopy(supplied['observations'])
    for name, value in session.value.get('resolved_observations', {}).items():
        value = recovery.frozen_observation(name, value)
        if name in observations and recovery.frozen_observation(name, observations[name]) != value:
            raise ValueError('已冻结的 Agent 观察不能在续接时改变：' + name)
        observations[name] = value
    discover(session, task, observations, work_root, install)
    guarded(session, 'default_model', lambda: default_model.resolve(session, task, observations))
    prepared = guarded(session, 'preparation', lambda: prepare(session, task, observations, work_root))
    if prepared and prepared['status'] == 'completed':
        for key in ('image_request', 'target', 'version_review', 'model_resolution'):
            recovery.freeze(session, key, observations[key])
    guarded(session, 'default_devices', lambda: default_devices.resolve(session, task, observations, work_root))
    jobs = data_jobs(session, task, observations, work_root)
    if prepared and prepared['status'] == 'completed' and not pending(task):
        guarded(session, 'migration', lambda: migrate(session, task, observations, jobs, work_root,
                                                     supplied))
        if session.ready('environment') and not session.ready('entry'):
            guarded(session, 'entry', lambda: precision(session, task, observations, jobs, work_root,
                                                       {**recovery.evaluations(session.previous),
                                                        **supplied.get('evaluation_results', {})}))
    guarded(session, 'task-quality', lambda: compare_all(session, task, observations, supplied, work_root, guarded))
    result = guarded(session, 'delivery', lambda: complete(session, task, observations, supplied, work_root, jobs))
    return result or session.finish('blocked', next_action=context.next_actions(['resolve_failed_steps']))


def run(args, source, raw, parent):
    supplied, session, task, conflicts, install = context.start(args, source, raw, parent)
    if session.previous and 'resolved_deployment' in session.previous:
        session.value['resolved_deployment'] = session.previous['resolved_deployment']
    if session.previous:
        session.value['resolved_observations'] = session.previous.get('resolved_observations', {})
    if supplied is None:
        return session.finish('blocked', next_action=context.next_actions(['intent', 'official_materials', 'datasets', 'deployment_recipe']))
    if conflicts:
        return session.finish('blocked', next_action={'owner': 'user', 'conflicts': conflicts})
    default_state.begin(session, task)
    session.value['context_source'] = sources.ref(args.context)
    session.value['delivery_supported'] = task['accuracy']['evaluator'] == 'evalscope'
    session.save()
    from coordination.runtime.runner import cancelled
    handlers = {sig: signal.signal(sig, cancelled) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return workflow(session, task, supplied, Path(args.work_root).expanduser().absolute(), install)
    except KeyboardInterrupt:
        with defer_interrupts([]):
            return session.finish('failed', next_action='inspect_owned_resources_after_cancellation')
    except EvidenceError:
        raise
    except ERRORS as error:
        return session.finish('blocked', diagnostic={'type': type(error).__name__, 'message': str(error)},
                              next_action=context.next_actions(['resolve_context_or_evidence']))
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
