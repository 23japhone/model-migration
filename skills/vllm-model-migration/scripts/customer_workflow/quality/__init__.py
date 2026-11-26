"""任务质量对齐的客户输入与原生测量准备。"""
from pathlib import Path

from coordination.runtime import sources
from .assets import catalog_info, load_source, prepare_data
from .compiler import compile_run, deployment
from .environment import environment
from .resolution import publish, resolve
from .upstream import bind_builtin


def history_record(ref):
    record = sources.bound(ref)
    if record.get('format') == 'evalscope-gpu-history-v1':
        return sources.bound(record['source'])
    if (record.get('task_id') == 'verify-agent-plus' and record.get('status') == 'completed'
            and record.get('conclusions', {}).get('evaluation_validity') == 'pass'):
        return sources.bound(record['evidence']['quality_evidence'])
    return {}


def historical(context, protocol):
    candidates, gpu = [], {}
    for ref in context.get('evidence', []):
        try:
            observed = history_record(ref)
            p = observed.get('protocol', {})
            if p.get('dataset') != protocol['dataset']:
                continue
            if observed.get('hardware') == 'gpu':
                gpu[ref['sha256']] = ref
            elif observed.get('hardware') == 'npu':
                candidates.append(ref)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            continue  # 可选历史材料也可以是文本；独立的 NPU 测量继续。
    return {'gpu_evidence': next(iter(gpu.values())) if len(gpu) == 1 else None, 'candidates': candidates}


def public_inputs(baseline, source, archive):
    from .baseline_search.qualification import inspected
    if not archive:
        return source, None
    history = inspected(archive)['protocol']
    if history['dataset'] != baseline['dataset']:
        raise ValueError('公开 GPU 归档与待重跑的评测任务不一致')
    metrics = dict(source.get('metrics', {}))
    for metric in {row['metric'] for row in baseline['scores']}:
        spec = history['metrics'][metric]
        metrics.setdefault(metric, spec)
    source = {**source, 'metrics': metrics}
    revision = history['dataset_revision']
    return source, {'dataset_revision': revision, 'gpu_evidence': archive}


def measurement_settings(baseline, source, context):
    # GPU 提供比较范围，本端原生工具和推理参数来自本端配置。
    settings = {key: baseline[key] for key in ('dataset', 'scores', 'subset_list') if key in baseline}
    settings.update(evaluator='evalscope')
    environment = source.get('evaluator_environment') or context.get('evaluator') or {}
    if environment.get('version') not in (None, 'latest'):
        settings['evaluator_version'] = environment['version']
    for key in ('prompt_config', 'generation_config', 'scoring_config'):
        if key in source:
            settings[key] = source[key]
    return settings


def requested_scopes(settings, available):
    wanted = settings.get('subset_list', available)
    subsets = [subset for subset in wanted if subset in available]
    if not subsets:
        raise ValueError('NPU 原生任务缺少要求的子集：' + '、'.join(wanted))
    if len(subsets) != len(wanted):
        settings['scores'] = [row for row in settings['scores'] if row.get('subset') in subsets]
        if not settings['scores']:
            missing = [subset for subset in wanted if subset not in subsets]
            raise ValueError('NPU 缺少总分所需子集或对应分项：' + '、'.join(missing))
    return subsets


def prepare(baseline, task, context, root, *, public_gpu=None):
    name = baseline['dataset']
    root = Path(root)
    source = load_source(context, name)
    if not source.get('repository'):
        raise ValueError('Agent 需按基线数据集取得官方仓库及该快照的原始文件清单：' + name)
    inputs = [*sources.paths(source, Path.cwd()), *sources.paths(context.get('evidence', []), Path.cwd()),
              *sources.paths(task.get('model', {}), Path.cwd()), Path(context['deployment_receipt']['path'])]
    inputs.extend(sources.paths(public_gpu, Path.cwd()))
    sources.protect(root, inputs)
    source, pinned = public_inputs(baseline, source, public_gpu)
    settings = measurement_settings(baseline, source, context)
    selected = resolve(settings, source, root, pinned=pinned)
    evaluator = environment(source, context, selected['evaluator_version'], root)
    catalog = catalog_info(source, name, evaluator, root)
    subsets = requested_scopes(settings, catalog['subset_names'])
    model = deployment(context, task)
    source = bind_builtin(name, source, selected, root)
    data = prepare_data(source, selected, catalog, subsets, root)
    request = compile_run(settings, source, catalog, selected, model, evaluator, data, root)
    request['measurement']['deployment'] = context['deployment_receipt']
    publish(root / 'protocol.json', request['protocol'])
    return {'protocol': request['protocol'], 'native_request': request, **historical(context, request['protocol'])}
