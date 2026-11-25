"""把声明协议绑定到原始配置、原生 TaskConfig 和准备的实际资产。"""
from ..evidence import bound, sha256
from ..inputs import canonical, dataset
from ..runtime.sample_mapping import source_rows

PROMPT_KEYS = ('system_prompt', 'prompt_template', 'few_shot_num', 'few_shot_random', 'few_shot_prompt_template')
SCORING_KEYS = ('filters', 'metric_list', 'aggregation')


def subset_assets(request):
    measurement = request['measurement']
    provided = request.get('datasets', {})
    assets = {t['subset']: dataset(provided.get(t['subset'], measurement['dataset'])) for t in measurement['tasks']}
    baseline = dataset(measurement['dataset'])
    for asset in assets.values():
        if (asset['path'], asset['identity']) != (baseline['path'], baseline['identity']):
            raise ValueError('一次原生 benchmark 需要完全相同的数据根及文件清单')
    return assets


def stable_ids(asset):
    records = asset['selection']['records']
    files = {item['file'] for item in records}
    if len(files) != 1:
        raise ValueError('原始选择无法绑定到唯一文件')
    path = files.pop()
    file = next(item for item in asset['files'] if item['path'] == path)
    rows = source_rows(asset, file)
    ids = []
    for record in records:
        i = record['row_index']
        row = rows[i]
        value = row.get('id') if isinstance(row, dict) else None
        if value is None:
            value = f'{path}#{i}'
        if type(value) not in (str, int) or value == '':
            raise ValueError('原始样本的显式 id 无效')
        ids.append(value)
    if len(ids) != len(set(ids)):
        raise ValueError('数据集子集内稳定样本 id 重复')
    return ids


def check_assets(p, model_files, assets):
    if canonical(model_files) != p['model_identity'] or set(assets) != set(p['subsets']):
        raise ValueError('权重、模型配置或子集身份与冻结协议不同')
    for subset, asset in assets.items():
        if asset['identity']['sha256'] != p['dataset_identity'] or asset['selection']['split'] != p['split']:
            raise ValueError('原始数据身份或 split 不同')
        if p['sample_identities'].get(subset) != asset['selection']['sha256']:
            raise ValueError('准备回执与协议的样本选择摘要不同')


def prompt_config(raw, args, generation):
    prompt = {'request_format': 'chat', **{key: args[key] for key in PROMPT_KEYS if key in args}}
    if 'chat_template' in raw:
        prompt['chat_template'] = raw['chat_template']
    kwargs = (generation.get('extra_body') or {}).get('chat_template_kwargs', {})
    if 'enable_thinking' in kwargs:
        prompt['enable_thinking'] = kwargs['enable_thinking']
    return prompt


def score_only(raw):
    if raw.get('collect_perf') or raw.get('debug'):
        raise ValueError('任务质量测量只允许原生评分，禁止收集性能或调试轨迹')
    generation = raw['generation_config']
    extra = generation.get('extra_body') or {}
    for config in (generation, extra):
        for key in ('logprobs','return_token_ids','return_tokens_as_token_ids','include_token_ids','echo'):
            if config.get(key):
                raise ValueError('任务质量测量不请求模型运行明细：' + key)
        for key in ('top_logprobs','prompt_logprobs'):
            if config.get(key) is not None:
                raise ValueError('任务质量测量不请求模型运行明细：' + key)


def check_config(p, raw, resolved, proof=None, adapter_sha256=None):
    args = raw['dataset_args'][p['dataset']]
    if args['subset_list'] != p['subsets'] or args['eval_split'] != p['split']:
        raise ValueError('执行配置的子集或 split 与协议不同')
    generation = raw['generation_config']
    if generation != p['generation_config']:
        raise ValueError('原配置的生成参数与协议不同')
    effective = resolved['generation_config']
    if any(effective.get(key) != val for key, val in generation.items()):
        raise ValueError('原生 TaskConfig 未保留所选生成参数')
    prompt = prompt_config(raw, args, generation)
    scoring = {'judge': raw['judge'], **{key: args[key] for key in SCORING_KEYS if key in args}}
    if proof is not None:
        expected = {'declaration': p['scoring_config']['declaration'], 'evalscope_version': p['evaluator_version'],
                    'benchmark': p['dataset'], 'adapter_sha256': adapter_sha256, 'scoring_config': scoring}
        if bound(proof) != expected:
            raise ValueError('自定义评分声明与已核验原生 adapter 或配置不同')
        scoring = {**scoring, 'declaration': expected['declaration']}
    if prompt != p['prompt_config'] or scoring != p['scoring_config']:
        raise ValueError('提示模板或评分配置与冻结协议不同')


def ref(path):
    return {'path': str(path), 'sha256': sha256(path)}
