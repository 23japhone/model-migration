"""客户 GPU 成绩与复测条件的静态合同；不解析版本或访问资产。"""
import math
import re

from ..defaults.policy import unspecified


def name(value, label):
    if not isinstance(value, str) or not value.strip() or value.strip() == '-1':
        raise ValueError(label + ' 须为非空文字')
    return value


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - {*required, *optional}:
        raise ValueError('GPU 基线字段缺失或未知：' + str(sorted(value) if isinstance(value, dict) else type(value).__name__))
    return dict(value)


def items(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(label + ' 须为非空数组')
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('评分或生成参数必须为有限数字')
    return value


def optional(value, *keys):
    for key in keys:
        if unspecified(value.get(key)):
            value.pop(key, None)


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(label + ' 不允许重复')
    return values


def sample(row, subset):
    value = fields(row, ('sample_id', 'score'), ('subset',))
    key = value['sample_id']
    if type(key) is not int:
        name(key, 'sample_id')
    if type(value['score']) is not bool:
        number(value['score'])
    if 'subset' in value:
        name(value['subset'], 'sample subset')
        if subset is not None and value['subset'] != subset:
            raise ValueError('明细子集与其分项冲突')
    return value


def score(row):
    value = fields(row, ('metric', 'gpu_score'), ('subset', 'target', 'sample_results'))
    name(value['metric'], 'metric')
    number(value['gpu_score'])
    optional(value, 'subset', 'target', 'sample_results')
    if 'subset' in value:
        name(value['subset'], 'subset')
    if value.get('target') == {}:
        value.pop('target')
    if 'target' in value:
        target = fields(value['target'], ('max_drop',))
        if number(target['max_drop']) < 0:
            raise ValueError('max_drop 须为非负数')
    if 'sample_results' in value:
        if not isinstance(value['sample_results'], list):
            raise ValueError('sample_results 须为数组')
        value['sample_results'] = [sample(r, value.get('subset')) for r in value['sample_results']]
    return value


def prompt_config(raw):
    value = fields(raw, (), ('request_format', 'enable_thinking', 'system_prompt', 'prompt_template', 'chat_template'))
    if 'request_format' in value and value['request_format'] not in ('chat', 'completion'):
        raise ValueError('request_format 须为 chat 或 completion')
    if 'enable_thinking' in value and type(value['enable_thinking']) is not bool:
        raise ValueError('enable_thinking 须为布尔值')
    for key in ('system_prompt', 'prompt_template', 'chat_template'):
        if key in value and not isinstance(value[key], str):
            raise ValueError(key + ' 须为模板文本')
    return value


def generation_config(raw):
    value = fields(raw, (), ('temperature', 'top_p', 'top_k', 'max_tokens', 'seed', 'stop'))
    for key in ('max_tokens', 'seed', 'top_k'):
        if key in value and type(value[key]) is not int:
            raise ValueError(key + ' 须为整数')
    if value.get('max_tokens', 1) <= 0 or number(value.get('temperature', 0)) < 0:
        raise ValueError('输出预算或温度超出范围')
    if not 0 < number(value.get('top_p', 1)) <= 1:
        raise ValueError('top_p 超出范围')
    if 'stop' in value:
        stops = value['stop']
        for stop in ([stops] if isinstance(stops, str) else items(stops, 'stop')):
            name(stop, 'stop')
    return value


def normalize_one(raw):
    value = fields(raw, ('evaluator', 'dataset', 'scores'),
                   ('evaluator_version', 'subset_list', 'task_name', 'task_revision',
                    'prompt_config', 'generation_config', 'scoring_config'))
    optional(value, 'evaluator_version', 'subset_list', 'task_name', 'task_revision',
             'prompt_config', 'generation_config', 'scoring_config')
    for key in ('evaluator', 'dataset', 'task_name', 'task_revision'):
        if key in value:
            name(value[key], key)
    value['evaluator'] = value['evaluator'].lower()
    if 'evaluator_version' in value and not re.fullmatch(r'[0-9][A-Za-z0-9.+!-]*', name(value['evaluator_version'], '版本')):
        raise ValueError('evaluator_version 须为精确发行版本')
    if 'subset_list' in value:
        value['subset_list'] = unique([name(s, 'subset') for s in items(value['subset_list'], 'subset_list')], '子集')
    if 'prompt_config' in value:
        value['prompt_config'] = prompt_config(value['prompt_config'])
    if 'generation_config' in value:
        value['generation_config'] = generation_config(value['generation_config'])
    if 'scoring_config' in value:
        cfg = fields(value['scoring_config'], (), ('scorer_name', 'scorer_version', 'answer_extraction',
                                                   'normalization', 'invalid_answer_policy'))
        for key, text in cfg.items():
            name(text, key)
    scores = [score(r) for r in items(value['scores'], 'scores')]
    unique([(s.get('subset'), s['metric']) for s in scores], '指标与分项组合')
    value['scores'] = scores
    return value
