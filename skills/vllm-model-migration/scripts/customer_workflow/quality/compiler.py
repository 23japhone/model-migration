"""将原生任务默认值和本端测量配置编译为一个全量 EvalScope 请求。"""
import copy

from coordination.runtime import sources
from ..compile.accuracy import model_id
from ..state import identity
from .resolution import publish

PROMPT_KEYS = ('system_prompt', 'prompt_template', 'few_shot_num', 'few_shot_random', 'few_shot_prompt_template')
SCORING_KEYS = ('filters', 'metric_list', 'aggregation')


def deployment(context, task):
    ref = context['deployment_receipt']
    report = sources.bound(ref)
    if report.get('operation') == 'review':
        report = sources.bound(report['inputs']['source'])
    link = {'path': report['evidence']['handoff'], 'sha256': report['detailed_report']['handoff_sha256']}
    result = sources.bound(link)
    if result['model']['name'] != task['model']['model_name']:
        raise ValueError('基线复测模型与顶层已验证部署不匹配')
    return result


def metric_definitions(source, scores, metadata):
    specs = {}
    aliases = metadata.get('metric_list') or []
    raw_names = [n for item in aliases for n in ([item] if isinstance(item, str) else item.keys())]
    for score in scores:
        name = score['metric']
        if name in specs:
            continue
        if name in source.get('metrics', {}):
            spec = source['metrics'][name]
        elif name in ('accuracy', 'acc') and len(set(raw_names) & {'acc', 'accuracy'}) == 1:
            key = next(n for n in raw_names if n in ('acc', 'accuracy'))
            spec = {'selector': {'name': 'accuracy', 'aggregation': 'mean', 'dimensions': {}},
                    'unit': 'ratio', 'direction': 'higher', 'sample_metric': key, 'sample_kind': 'binary'}
        else:
            continue  # 报告将该指标列为缺项，已有映射的其他分项仍可测量。
        specs[name] = spec
    if not specs:
        raise ValueError('没有可测量的原生指标映射')
    return specs


def config_args(base, source, metadata, subsets, path):
    args = {key: metadata[key] for key in (*PROMPT_KEYS, *SCORING_KEYS)
            if metadata.get(key) is not None}
    overrides = source.get('task_args', {})
    if set(overrides) - {*PROMPT_KEYS, *SCORING_KEYS}:
        raise ValueError('原生任务配置含未纳入质量协议的字段')
    args.update(overrides)
    prompt = base.get('prompt_config', {})
    args.update({key: prompt[key] for key in ('prompt_template', 'system_prompt') if key in prompt})
    args.update(local_path=path, subset_list=subsets)
    return args


def native_generation(base, native_defaults):
    generation = copy.deepcopy(native_defaults)
    for key, value in base.get('generation_config', {}).items():
        if key == 'stop':
            generation['stop_seqs'] = [value] if isinstance(value, str) else value
        else:
            generation[key] = value
    thinking = base.get('prompt_config', {}).get('enable_thinking')
    if thinking is not None:
        generation.setdefault('extra_body', {}).setdefault('chat_template_kwargs', {})['enable_thinking'] = thinking
    limit = generation.get('max_tokens')
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('已有的原生输出预算必须为正整数')
    generation.update(stream=False, retries=1, n=1)
    return generation


def configuration(base, source, catalog, subsets, shared, model):
    name = base['dataset']
    prompt = base.get('prompt_config', {})
    if prompt.get('request_format', 'chat') != 'chat':
        raise ValueError('EvalScope 当前原生质量入口仅支持 chat；completion 需要对应实现')
    args = config_args(base, source, catalog['metadata'], subsets, shared['path'])
    args['eval_split'] = catalog['eval_split']
    generation = native_generation(base, catalog.get('generation_defaults', {}))
    conf = {'model': model['model']['name'], 'model_id': model_id(model['model']['name']),
            'api_url': model['endpoint'].rstrip('/') + '/v1', 'api_key': 'EMPTY', 'eval_type': 'openai_api',
            'eval_backend': 'Native', 'model_task': 'text_generation', 'datasets': [name],
            'dataset_args': {name: args}, 'limit': None, 'generation_config': generation,
            'eval_batch_size': 1, 'repeats': 1, 'collect_perf': False,
            'judge': {'strategy': 'rule'}, 'model_args': {'max_retries': 0}}
    if 'chat_template' in prompt:
        conf['chat_template'] = prompt['chat_template']
        generation.setdefault('extra_body', {})['chat_template'] = prompt['chat_template']
    return conf


def prompt_conditions(config, args):
    prompt = {'request_format': 'chat', **{key: args[key] for key in PROMPT_KEYS if key in args}}
    if 'chat_template' in config:
        prompt['chat_template'] = config['chat_template']
    opts = config['generation_config'].get('extra_body', {}).get('chat_template_kwargs', {})
    if 'enable_thinking' in opts:
        prompt['enable_thinking'] = opts['enable_thinking']
    return prompt


def scoring_conditions(base, source, catalog, config, args):
    scoring = {'judge': config['judge'], **{key: args[key] for key in SCORING_KEYS if key in args}}
    declaration = base.get('scoring_config')
    if not declaration:
        return scoring, {}
    if not source.get('scoring_proof'):
        raise ValueError('自定义评分规则尚未绑定到原生任务实现及其配置')
    proof = sources.bound(source['scoring_proof'])
    expected = {'declaration': declaration, 'evalscope_version': catalog['evaluator_version'],
                'benchmark': base['dataset'], 'adapter_sha256': catalog['adapter_source']['sha256'],
                'scoring_config': scoring}
    if proof != expected:
        raise ValueError('实测版本、原生评分器或 本端显式规则与评分配置映射不同')
    return {**scoring, 'declaration': declaration}, {'scoring_proof': source['scoring_proof']}


def compile_run(base, source, catalog, selected, model, evaluated, data, root):
    shared, receipts, selection = data
    subsets = list(receipts)
    name = base['dataset']
    config = configuration(base, source, catalog, subsets, shared, model)
    args = config['dataset_args'][name]
    metrics = metric_definitions(source, base['scores'], catalog['metadata'])
    config_ref = publish(root / 'configs' / (identity(config) + '.json'), config)
    scoring, proof = scoring_conditions(base, source, catalog, config, args)
    task_revision = catalog['metadata'].get('evaluation_version') or selected['evaluator_version']
    if source.get('task_revision', task_revision) != task_revision:
        raise ValueError('本端任务实现版本与原生配置不同')
    task_name = catalog['metadata'].get('name') or catalog['name']
    if source.get('task_name', task_name) != task_name:
        raise ValueError('指定的任务变体缺少对应原生实现与配置')
    protocol = {'evaluator': 'evalscope', 'evaluator_version': selected['evaluator_version'],
                'dataset': name, 'dataset_revision': selected['dataset_revision'],
                'dataset_identity': shared['identity']['sha256'], 'task_name': task_name, 'task_revision': task_revision,
                'subsets': subsets, 'split': args['eval_split'], 'model_identity': identity(model['model']['files']),
                'sample_identities': {s: row['sha256'] for s, row in selection.items()},
                'prompt_config': prompt_conditions(config, args), 'generation_config': config['generation_config'],
                'scoring_config': scoring, 'metrics': metrics}
    native_metrics = {metric: {k: spec[k] for k in ('selector', 'unit', 'direction')} for metric, spec in metrics.items()}
    tasks = [{'dataset': 'quality-' + str(i), 'benchmark': name, 'subset': subset,
              'model_abbr': config['model_id'], 'metrics': native_metrics,
              'sample_ids': list(range(selection[subset]['count']))} for i, subset in enumerate(subsets)]
    measurement = {'schema_version': '1.0', 'dataset': receipts[subsets[0]], 'tasks': tasks,
                   'evaluator': {'python': evaluated['python'], 'backend': 'evalscope',
                                 'version': selected['evaluator_version'], 'config': config_ref}}
    scores = [{**metrics[row['metric']], 'metric': row['metric'], 'subset': row.get('subset')}
              for row in base['scores'] if row['metric'] in metrics and row.get('subset') in (None, *subsets)]
    if not scores:
        raise ValueError('所选 NPU 数据集中没有对应分项')
    return {'schema_version': '1.0', 'measurement': measurement, 'protocol': protocol, 'scores': scores, 'datasets': receipts, **proof}
