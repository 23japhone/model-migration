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


def compatible(source, protocol):
    conditions = dict(protocol)
    requested = conditions.pop('metrics')
    if any(source.get(k) != v for k, v in conditions.items()):
        return False
    available = source.get('metrics', {})
    return all(available.get(k) == v for k, v in requested.items())


def historical(context, protocol):
    candidates, gpu, matches = [], {}, {}
    for ref in context.get('evidence', []):
        try:
            observed = history_record(ref)
            p = observed.get('protocol', {})
            if p.get('dataset') != protocol['dataset']:
                continue
            if observed.get('hardware') == 'gpu':
                gpu[ref['sha256']] = ref
                if compatible(p, protocol):
                    matches[ref['sha256']] = ref
            elif observed.get('hardware') == 'npu':
                candidates.append(ref)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            continue  # 可选历史材料也可以是文本；独立的 NPU 测量继续。
    pool = matches or gpu
    return {'gpu_evidence': next(iter(pool.values())) if len(pool) == 1 else None, 'candidates': candidates}


def public_inputs(baseline, source, archive):
    from .baseline_search.qualification import inspected
    if not archive:
        return source, None
    history = inspected(archive)['protocol']
    if history['dataset'] != baseline['dataset'] or history['evaluator_version'] != baseline['evaluator_version']:
        raise ValueError('公开 GPU 归档与待重跑的评测任务不一致')
    metrics = dict(source.get('metrics', {}))
    for metric in {row['metric'] for row in baseline['scores']}:
        spec = history['metrics'][metric]
        if metric in metrics and metrics[metric] != spec:
            raise ValueError('原生指标映射与公开 GPU 历史配置不同：' + metric)
        metrics[metric] = spec
    source = {**source, 'metrics': metrics}
    revision = history['dataset_revision']
    return source, {'dataset_revision': revision, 'gpu_evidence': archive}


def prepare(baseline, task, context, root, *, public_gpu=None):
    if baseline['evaluator'].lower() != 'evalscope':
        raise ValueError('质量复测当前只支持 EvalScope 原生执行；保留其他工具的 GPU 声明')
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
    selected = resolve(baseline, source, root, pinned=pinned)
    evaluator = environment(source, context, selected['evaluator_version'], root)
    catalog = catalog_info(source, name, evaluator, root)
    subsets = baseline.get('subset_list', catalog['subset_names'])
    if not subsets or set(subsets) - set(catalog['subset_names']):
        raise ValueError('基线所选子集未绑定原生任务')
    if any(row.get('subset') is not None and row['subset'] not in subsets for row in baseline['scores']):
        raise ValueError('成绩子集不在基线评测范围内')
    model = deployment(context, task)
    source = bind_builtin(name, source, selected, root)
    data = prepare_data(source, selected, catalog, subsets, root)
    request = compile_run(baseline, source, catalog, selected, model, evaluator, data, root)
    request['measurement']['deployment'] = context['deployment_receipt']
    publish(root / 'protocol.json', request['protocol'])
    return {'protocol': request['protocol'], 'native_request': request, **historical(context, request['protocol'])}
