"""下游配置形状；实际业务身份另由部署和精度绑定核验。"""
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from coordination.request import fields


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, field):
    require(isinstance(value, str) and bool(value.strip()) and '\0' not in value,
            field + ' 须为非空字符串')
    return value


def integer(value, field, minimum=1):
    require(type(value) is int and value >= minimum, field + ' 整数范围无效')


def absolute(value, field):
    path = Path(text(value, field))
    require(path.is_absolute() and str(path) == value and '..' not in path.parts,
            field + ' 须为实际执行环境的规范绝对路径')
    return path


def service(value):
    fields(value, ('base_url', 'model', 'request_timeout_seconds'))
    url = urlsplit(text(value['base_url'], 'service.base_url'))
    require(url.scheme in ('http', 'https') and url.hostname and url.path in ('', '/')
            and not (url.query or url.fragment or url.username or url.password),
            'service.base_url 须为无凭据、无 API 路径的服务根地址')
    require(url.hostname not in ('0.0.0.0', '::'), '服务访问地址不能使用监听通配地址')
    try:
        port = url.port
    except ValueError as error:
        raise ValueError('服务端口格式无效') from error
    require(port is not None, '服务地址须显式声明端口')
    text(value['model'], 'service.model')
    integer(value['request_timeout_seconds'], 'service.request_timeout_seconds')


def benchmark(value):
    fields(value, ('service', 'tokenizer_path', 'benchmark', 'output_dir', 'runtime_metrics'))
    service(value['service'])
    tokenizer = absolute(value['tokenizer_path'], 'tokenizer_path')
    require(tokenizer.is_dir() and (tokenizer/'tokenizer_config.json').is_file(),
            'tokenizer_path 缺少可访问的 tokenizer_config.json')
    require(value['output_dir'] == '../outputs', '交接 output_dir 固定为 ../outputs')
    bench = value['benchmark']
    fields(bench, ('concurrency', 'requests_per_run', 'warmup_requests', 'input_tokens',
                   'output_tokens', 'seed', 'enable_thinking', 'ignore_eos', 'timeout_seconds'))
    concurrency = bench['concurrency']
    require(isinstance(concurrency, list) and bool(concurrency), 'benchmark.concurrency 须为非空数组')
    for item in concurrency:
        integer(item, 'benchmark.concurrency')
    require(len(set(concurrency)) == len(concurrency), 'benchmark.concurrency 不允许重复值')
    for key in ('requests_per_run', 'input_tokens', 'output_tokens', 'timeout_seconds'):
        integer(bench[key], 'benchmark.'+key)
    for key in ('warmup_requests', 'seed'):
        integer(bench[key], 'benchmark.'+key, 0)
    for key in ('enable_thinking', 'ignore_eos'):
        require(type(bench[key]) is bool, 'benchmark.'+key+' 须为布尔值')
    metrics(value['runtime_metrics'])


def metrics(value):
    fields(value, ('enabled', 'interval_sec', 'timeout_sec'))
    require(type(value['enabled']) is bool, 'runtime_metrics.enabled 须为布尔值')
    for key in ('interval_sec', 'timeout_sec'):
        number = value[key]
        require(type(number) in (int, float) and math.isfinite(number) and number > 0,
                'runtime_metrics.'+key+' 须为有限正数')


def accuracy(value):
    fields(value, ('service', 'evaluation', 'output_dir'))
    service(value['service'])
    require(value['output_dir'] == '../outputs', '交接 output_dir 固定为 ../outputs')
    evaluation = value['evaluation']
    fields(evaluation, ('model_path', 'api_key', 'timeout_seconds', 'datasets'))
    text(evaluation['model_path'], 'evaluation.model_path')
    require(evaluation['api_key'] in ('', 'EMPTY'), '交接不能包含真实 API key')
    integer(evaluation['timeout_seconds'], 'evaluation.timeout_seconds')
    datasets = evaluation['datasets']
    require(isinstance(datasets, list) and bool(datasets), 'evaluation.datasets 须为非空数组')
    names = []
    for item in datasets:
        accuracy_dataset(item)
        names.append(item['name'])
    require(len(set(names)) == len(names), 'evaluation.datasets 名称须唯一')


def accuracy_dataset(value):
    fields(value, ('name', 'evaluator', 'limit', 'eval_batch_size', 'dataset', 'subset_list',
                   'dataset_args', 'extra_args'))
    require('/' not in text(value['name'], 'dataset.name') and '\\' not in value['name'],
            '数据集名称不能包含路径分隔符')
    require(value['evaluator'] == 'evalscope', '当前交接仅能无损表达 EvalScope 基线')
    if value['limit'] is not None:
        integer(value['limit'], 'dataset.limit')
    integer(value['eval_batch_size'], 'dataset.eval_batch_size')
    require(absolute(value['dataset'], 'dataset.dataset').exists(), '本地基线数据路径不可访问')
    subsets = value['subset_list']
    require(isinstance(subsets, list) and bool(subsets), 'subset_list 须为非空数组')
    for item in subsets:
        text(item, 'subset_list')
    require(len(set(subsets)) == len(subsets), 'subset_list 不允许重复')
    require(isinstance(value['dataset_args'], dict), 'dataset_args 须为对象')
    require(not {'local_path', 'dataset_id', 'subset_list'} & value['dataset_args'].keys(),
            '本地路径和子集须使用专用字段')
    require(isinstance(value['extra_args'], list), 'extra_args 须为字符串数组')
    for item in value['extra_args']:
        text(item, 'extra_args')


def deployment(value, runtime_paths=False):
    fields(value, ('cann_path', 'env_variables', 'log_file', 'server'))
    path = absolute(value['cann_path'], 'cann_path')
    require(runtime_paths or path.exists(), 'cann_path 不可访问')
    require(value['log_file'] == '../outputs/server.log', '交接 log_file 固定为 ../outputs/server.log')
    env = value['env_variables']
    require(isinstance(env, dict), 'env_variables 须为对象')
    for key, item in env.items():
        require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) is not None,
                '环境变量名无效')
        require(isinstance(item, str) and '\0' not in item, '环境变量值须为无 NUL 的字符串')
    server = value['server']
    require(isinstance(server, dict), 'server 须为对象')
    for field in ('model_path', 'served_model_name', 'host'):
        text(server.get(field), 'server.'+field)
    absolute(server['model_path'], 'server.model_path')
    integer(server.get('port'), 'server.port')
    require(server['port'] <= 65535, 'server.port 超出范围')
