"""EvalScope 配置支持边界；校验不改写模板、评分或生成参数。"""
import math
from pathlib import Path

from ...evidence import decode

GENERATION_FIELDS = {'stream', 'n', 'retries', 'retry_interval', 'timeout', 'max_tokens',
                     'temperature', 'top_p', 'top_k', 'stop_seqs', 'frequency_penalty',
                     'presence_penalty', 'repetition_penalty', 'logit_bias', 'seed',
                     'logprobs', 'top_logprobs', 'reasoning_effort', 'response_schema', 'extra_body'}


def object_field(value, key, default=None):
    result = value.get(key, {} if default is None else default)
    if not isinstance(result, dict):
        raise ValueError(key + ' 必须为对象')
    return result


def load_config(path):
    path = Path(path)
    if path.suffix == '.json':
        return decode(path.read_bytes())
    if path.suffix not in ('.yaml', '.yml'):
        raise ValueError('EvalScope 配置必须为 JSON 或 YAML')
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        pairs = loader.construct_pairs(node, deep=True)
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('YAML 配置含重复字段')
            result[key] = value
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(path.read_text(encoding='utf-8'), Loader=UniqueLoader)


def generation(config, *, allow_native_budget=False):
    value = object_field(config, 'generation_config')
    if set(value) - GENERATION_FIELDS:
        raise ValueError('generation_config 含未支持或原生 OpenAI 接口不生效的字段')
    single = all(type(value.get(key)) == int and value[key] == 1 for key in ('n', 'retries'))
    if value.get('stream') is not False or not single:
        raise ValueError('须显式 stream=false、n=1、retries=1，禁止隐式重复请求')
    maximum = value.get('max_tokens')
    if maximum is not None or not allow_native_budget:
        if type(maximum) is not int or maximum <= 0:
            raise ValueError('须显式配置正整数 max_tokens')
    extra = object_field(value, 'extra_body')
    forbidden = {'model', 'messages', 'stream', 'n', 'max_tokens', 'max_completion_tokens',
                 'truncate_prompt_tokens', 'best_of', 'tools', 'tool_choice', 'modalities', 'audio'}
    if set(extra) & forbidden:
        raise ValueError('extra_body 不得覆盖模型、样本、生成次数或截断边界')
    model_args = object_field(config, 'model_args')
    if type(model_args.get('max_retries')) != int or model_args['max_retries'] != 0 or set(model_args) - {'max_retries', 'timeout'}:
        raise ValueError('model_args 仅支持显式 max_retries=0 和可选 timeout')


def validate_limit(value):
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError('limit 须为正整数；省略或 null 表示所选 split/subset 全量')


def execution(config):
    if config.get('eval_type') != 'openai_api' or config.get('eval_backend') != 'Native':
        raise ValueError('仅支持 Native / openai_api')
    if type(config.get('repeats', 1)) != int or config.get('repeats', 1) != 1 or config.get('use_cache') or config.get('rerun_review'):
        raise ValueError('新测量禁止重复生成、缓存复用或重新评分')
    disabled = ('agent_config', 'use_sandbox', 'eval_config', 'analysis_report', 'ignore_errors', 'stream')
    if any(config.get(key) for key in disabled) or object_field(config, 'sandbox').get('enabled'):
        raise ValueError('配置包含首版不支持的执行能力')
    judge = object_field(config, 'judge')
    if judge.get('strategy') != 'rule' or judge.get('models'):
        raise ValueError('首版须显式 judge.strategy=rule，不调用外部评分模型')
    if config.get('model_task', 'text_generation') != 'text_generation':
        raise ValueError('首版仅支持文本生成')
    validate_limit(config.get('limit'))


def validate(config, tasks, handoff, *, allow_native_budget=False):
    if not isinstance(config, dict):
        raise ValueError('TaskConfig 必须为对象')
    execution(config); generation(config, allow_native_budget=allow_native_budget)
    if config.get('api_key', 'EMPTY') not in ('EMPTY', ''):
        raise ValueError('首版不接受认证凭据配置')
    if config.get('model') != handoff['model']['name']:
        raise ValueError('实际 served-model-name 与交接不一致')
    if config.get('api_url', '').rstrip('/') != handoff['endpoint'].rstrip('/') + '/v1':
        raise ValueError('api_url 必须为交接端点的 /v1 base URL')
    expected = {task['benchmark'] for task in tasks}
    datasets = config.get('datasets', [])
    if len(datasets) != len(set(datasets)) or set(datasets) != expected:
        raise ValueError('TaskConfig datasets 与声明的 benchmark 不一致')
    if set(object_field(config, 'dataset_args')) != expected:
        raise ValueError('每个 benchmark 须明确 dataset_args')
    for task in tasks:
        if config.get('model_id') != task['model_abbr']:
            raise ValueError('model_id 必须与 model_abbr 一致')
        name = task['benchmark']; args = object_field(config['dataset_args'], name)
        subsets = [item['subset'] for item in tasks if item['benchmark'] == name]
        if len(subsets) != len(set(subsets)) or args.get('subset_list') != subsets:
            raise ValueError('subset_list 与声明任务不一致')


def finite(value):
    if isinstance(value, dict):
        for item in value.values(): finite(item)
    elif isinstance(value, list):
        for item in value: finite(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('配置不允许非有限数字')


def native_config(raw):
    from evalscope import TaskConfig
    from evalscope.api.benchmark import BenchmarkMeta
    from dataclasses import fields
    if set(raw) - set(TaskConfig.model_fields):
        raise ValueError('TaskConfig 含未知字段')
    allowed = {field.name for field in fields(BenchmarkMeta)} | {'local_path'}
    for args in raw.get('dataset_args', {}).values():
        if set(args) - allowed:
            raise ValueError('dataset_args 含原生会忽略的未知字段')
    finite(raw)
    config = TaskConfig(**{**raw, 'limit': raw.get('limit')})
    return config
