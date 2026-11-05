"""由已核验部署与原生基线生成三文件，负载采用 customer-baseline-v1。"""
import json
from device_contract import effective_runtime

from . import accuracy, contracts, deployment, provenance, runtime_environment


def workload(capacity):
    for key in ('max_model_len', 'max_num_seqs', 'kv_token_capacity'):
        contracts.integer(capacity.get(key), key)
    context = capacity['max_model_len']
    contracts.require(context >= 2, '服务上下文不足以生成压测负载')
    input_tokens = min(1024, context // 2)
    output_tokens = min(1024, context - input_tokens)
    limit = min(64, capacity['max_num_seqs'], capacity['kv_token_capacity'] // (input_tokens + output_tokens))
    contracts.require(limit >= 1, '已核验 KV 容量不足以容纳单个压测请求')
    concurrency, value = [], 1
    while value <= limit:
        concurrency.append(value)
        value *= 2
    maximum = concurrency[-1]
    available = capacity.get('runtime_metrics_available')
    contracts.require(type(available) is bool, '缺少实际运行指标能力观察')
    return {'benchmark': {'concurrency': concurrency, 'requests_per_run': max(32, 4 * maximum),
                          'warmup_requests': min(8, maximum), 'input_tokens': input_tokens,
                          'output_tokens': output_tokens, 'seed': 42, 'enable_thinking': False,
                          'ignore_eos': True, 'timeout_seconds': 3600},
            'runtime_metrics': {'enabled': available, 'interval_sec': 1, 'timeout_sec': 10}}


def argument_value(values):
    if not values:
        return True
    if len(values) > 1:
        return values
    value = values[0]
    if value.lstrip().startswith(('{', '[')):
        return json.loads(value)
    return value


def server(original):
    options = deployment.options(deployment.arguments(original['service']['command']))
    value = {('model_path' if key == '--model' else key[2:].replace('-', '_')): argument_value(items)
             for key, items in options.items()}
    value['port'] = int(value['port'])
    return value


def cann_path(original, handoff, environment_source):
    values = runtime_environment.paths(original['runtime'].get('env', {}))
    if environment_source:
        values += runtime_environment.paths(runtime_environment.environment(environment_source, original, handoff))
    contracts.require(bool(values) and len(set(values)) == 1, '缺少唯一且与部署绑定的 CANN 路径')
    return values[0]


def evaluation(raws, handoff):
    datasets, timeout = accuracy.expected_datasets(raws, handoff['model']['name'], handoff['endpoint'].rstrip('/'))
    for row in datasets:
        extra = []
        for flag, value in row['extra_args'].items():
            extra.extend([flag, json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                          if flag in accuracy.JSON_FLAGS.values() else value])
        row['extra_args'] = extra
    return {'model_path': handoff['model']['name'], 'api_key': 'EMPTY',
            'timeout_seconds': timeout, 'datasets': datasets}


def generate(entry, capacity, environment_source=None):
    _, original, handoff, baselines = provenance.load(entry)
    actual = server(original)
    for key in ('max_model_len', 'max_num_seqs'):
        if key in actual:
            contracts.require(capacity[key] <= int(actual[key]), '压测容量超出实际启动参数 ' + key)
    service = {'base_url': handoff['endpoint'].rstrip('/'), 'model': handoff['model']['name'],
               'request_timeout_seconds': 600}
    benchmark = {'service': dict(service), 'tokenizer_path': handoff['model']['root'],
                 'output_dir': '../outputs', **workload(capacity)}
    plan = {'service': dict(service), 'evaluation': evaluation(baselines, handoff), 'output_dir': '../outputs'}
    deployed = {'cann_path': cann_path(original, handoff, environment_source),
                'env_variables': effective_runtime(handoff, original)['env'], 'log_file': '../outputs/server.log',
                'server': actual}
    deployment.validate(deployed, benchmark, plan, original, handoff, environment_source)
    accuracy.validate(plan, baselines, handoff)
    return {'benchmark_plan': benchmark, 'accuracy_plan': plan, 'deployment': deployed}
