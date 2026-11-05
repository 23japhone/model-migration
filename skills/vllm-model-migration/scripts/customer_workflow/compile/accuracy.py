"""客户精度统一方案与逐子集请求；不替代原生 loader 或评分器。"""
import copy
import hashlib
import re

from .generation import OUTPUT_TOKENS


def model_id(name):
    return name if re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name) else (
        'model-' + hashlib.sha256(name.encode()).hexdigest()[:24])


def task_config(task, job, endpoint, thinking_supported):
    dataset, subset, catalog = job['dataset'], job['subset'], job['catalog']
    args = copy.deepcopy(catalog.get('dataset_args', {}))
    args.update(local_path=job['prepared']['asset']['path'], subset_list=[subset['name']],
                eval_split=subset['split'], few_shot_num=0)
    generation = {'stream': False, 'n': 1, 'retries': 1, 'max_tokens': OUTPUT_TOKENS,
                  'temperature': 0, 'top_p': 1, 'seed': 42}
    if thinking_supported:
        generation['extra_body'] = {'chat_template_kwargs': {'enable_thinking': False}}
    name = task['model']['model_name']
    return {'model': name, 'model_id': model_id(name), 'api_url': endpoint.rstrip('/') + '/v1', 'api_key': 'EMPTY',
            'eval_type': 'openai_api', 'eval_backend': 'Native', 'model_task': 'text_generation',
            'datasets': [dataset['name']], 'dataset_args': {dataset['name']: args},
            'limit': dataset['limit'], 'eval_batch_size': 1, 'repeats': 1, 'seed': 42,
            'generation_config': generation, 'model_args': {'max_retries': 0}, 'judge': {'strategy': 'rule'}}


def request(task, job, prepared, evaluator, endpoint, session, thinking_supported):
    asset = prepared['asset']
    selected = asset['selection']
    if not selected['records'] or {row['file'] for row in selected['records']} != {job['subset']['source_file']}:
        raise ValueError('数据准备没有唯一绑定原生子集原文件')
    count = selected['count']
    if [row['row_index'] for row in selected['records']] != list(range(count)):
        raise ValueError('精度数据须为本子集前 N 条或全量')
    config = task_config(task, {**job, 'prepared': prepared}, endpoint, thinking_supported)
    source = session.artifact(job['id'] + '-evalscope', config)
    backend = task['accuracy']['evaluator']
    if backend != 'evalscope':
        # AISBench 继续消费 Agent 依据原契约生成并核对的原生 Python 配置。
        source = job['catalog']['aisbench_config']
    tool = {'python': evaluator['python'], 'backend': backend, 'version': evaluator['version'],
            'config': source, 'timeout_seconds': 14400}
    item = {'dataset': job['id'], 'model_abbr': model_id(task['model']['model_name']),
            'sample_ids': list(range(count)), 'metrics': job['subset']['metrics']}
    if backend == 'evalscope':
        item.update(benchmark=job['dataset']['name'], subset=job['subset']['name'])
    else:
        item['source_columns'] = job['subset']['source_columns']
    return {'schema_version': '1.0', 'dataset': job['receipt'], 'evaluator': tool, 'tasks': [item], 'targets': []}


def context_tokens(jobs):
    lengths = [job['subset']['max_prompt_tokens'] for job in jobs]
    if not lengths or any(type(n) is not int or n < 1 for n in lengths):
        raise ValueError('缺少原生评测样本的真实 tokenizer 长度')
    return max(lengths) + OUTPUT_TOKENS
