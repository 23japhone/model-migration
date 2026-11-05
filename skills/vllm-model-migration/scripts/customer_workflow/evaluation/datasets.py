"""准备回执绑定正式原生 catalog，预算不消费准备前的观察值。"""
import copy

from .. import effects, recovery
from ..compile import assets


def reusable_request(previous, requested):
    if previous == requested:
        return True
    upgraded = {**previous, 'layout': 'evalscope'}
    if previous.get('layout', 'raw') == 'raw' and upgraded == requested:
        return False
    raise ValueError('数据准备请求或子集顺序已变化，不能复用另一项的旧回执')


def catalog_request(task, job, reference, observations):
    dataset = copy.deepcopy(job['dataset'])
    dataset['subset_list'] = [job['subset']['name']]
    args = copy.deepcopy(job['catalog'].get('dataset_args', {}))
    args.pop('local_path', None)
    args.update(eval_split=job['subset']['split'], few_shot_num=0)
    return {'model': task['model']['model_name'],
            'model_path': observations['model_resolution']['request']['path'],
            'dataset': dataset, 'dataset_manifest': reference, 'dataset_args': args}


def bound_subset(result, request, job, prepared, evaluator):
    if result['inputs'] != request or result.get('dataset_manifest') != request['dataset_manifest']:
        raise ValueError('正式原生目录未绑定本次准备回执或请求')
    root = prepared['asset']['path']
    if result['local_path'] != root or result['evaluator_version'] != evaluator['version']:
        raise ValueError('正式原生目录的数据根或评测版本不同')
    assets.catalog_files(root, result['data_files'])
    name = job['subset']['name']
    if result['subset_names'] != [name] or len(result['subsets']) != 1:
        raise ValueError('正式目录必须覆盖本项唯一子集')
    subset = result['subsets'][0]
    if subset['name'] != name or subset['split'] != job['subset']['split']:
        raise ValueError('正式原生目录的子集或 split 不一致')
    length = subset['max_prompt_tokens']
    if type(length) is not int or length < 1:
        raise ValueError('正式目录缺少真实 token 长度')
    return {**job['subset'], 'max_prompt_tokens': length}


def bind_prepared(session, task, job, prepared, observations, work_root):
    recovery.asset_files(prepared['asset'])
    reference = session.value['steps'][job['id']]['receipt']
    values = {'prepared': prepared, 'receipt': reference}
    if task['accuracy']['evaluator'] != 'evalscope':
        return values
    request = catalog_request(task, job, reference, observations)
    evaluator = observations['evaluator']
    result = effects.operation(session, job['id'] + '-catalog', 'verify-agent-plus', request, work_root,
                               python=evaluator['python'], entry='catalog.py')
    values['subset'] = bound_subset(result, request, job, prepared, evaluator)
    return values
