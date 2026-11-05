"""将冻结的 EvalScope 基线条件映射到下游 Plan；不推测评分或样本。"""
from coordination.runtime import sources
from .contracts import require
from . import baseline_data

# 下游 EvalScope 分支仅直接传递 limit、eval_batch_size、dataset_args；其余通过 extra_args。
JSON_FLAGS = {'generation_config': '--generation-config', 'model_args': '--model-args', 'judge': '--judge'}
TEXT_FLAGS = {'seed': '--seed', 'repeats': '--repeats', 'model_task': '--model-task',
              'model_id': '--model-id', 'chat_template': '--chat-template', 'eval_backend': '--eval-backend'}


def extra_arguments(config):
    result = {}
    for key, flag in JSON_FLAGS.items():
        require(isinstance(config.get(key), dict), '冻结配置缺少 '+key)
        result[flag] = config[key]
    for key, flag in TEXT_FLAGS.items():
        value = config.get(key)
        if value is not None:
            result[flag] = str(value)
    return result


def parsed_extra(values):
    require(len(values) % 2 == 0, 'extra_args 须为无重复的选项和值对')
    result = {}
    allowed = set(JSON_FLAGS.values()) | set(TEXT_FLAGS.values())
    for flag, value in zip(values[::2], values[1::2]):
        require(flag in allowed and flag not in result, 'extra_args 含覆盖基础输入或未支持的选项')
        result[flag] = sources.parse(value) if flag in JSON_FLAGS.values() else value
    return result


def frozen_tasks(raw, model, endpoint):
    details = raw['detailed_report']
    require(details.get('conditions', {}).get('evaluator_backend') == 'evalscope',
            'AISBench 或缺少工具身份的基线不能自动转换为 EvalScope')
    asset = baseline_data.checked(raw)
    tasks = details.get('tasks', [])
    declared = {task['dataset']: task for task in raw['inputs']['tasks']}
    require(tasks and len(tasks) == len(declared), '冻结任务与基线声明数量不一致')
    result = []
    for task in tasks:
        expected = declared.get(task['dataset'])
        require(expected is not None and all(task.get(key) == item for key, item in expected.items()),
                '冻结任务与原始精度请求不一致')
        path = task['config']
        digest = details['files'].get(path)
        require(digest is not None, '冻结评测配置没有原始文件摘要')
        config = sources.bound({'path': path, 'sha256': digest})
        require(config.get('model') == model and config.get('api_url', '').rstrip('/') == endpoint+'/v1',
                '冻结评测配置服务身份不一致')
        require(config.get('eval_type') == 'openai_api' and config.get('eval_backend') == 'Native',
                '仅支持 Native / openai_api 基线')
        require(config.get('datasets') == [task['benchmark']], '冻结配置 benchmark 不一致')
        require(config.get('api_key', 'EMPTY') in ('EMPTY', ''), '冻结配置含认证凭据')
        for key, item in details['conditions']['model'].items():
            require(config.get(key) == item, '冻结配置与实际基线生成条件不一致')
        baseline_data.local_path(config, task, asset)
        result.append((task, config))
    require(len({task['dataset'] for task, _ in result}) == len(result), '冻结任务名称重复')
    return result


def dataset(task, config):
    name = task['benchmark']
    args = dict(config['dataset_args'][name])
    require(args.get('subset_list') == [task['subset']], '冻结配置子集不一致')
    path = args.pop('local_path')
    subsets = args.pop('subset_list')
    require('dataset_id' not in args, '冻结 dataset_id 不能替代本地路径交接')
    require(type(config.get('eval_batch_size')) is int, '冻结配置缺少实际 eval_batch_size')
    return {'name': name, 'evaluator': 'evalscope', 'dataset': path, 'subset_list': subsets,
            'dataset_args': args, 'limit': config.get('limit'), 'eval_batch_size': config['eval_batch_size'],
            'extra_args': extra_arguments(config)}


def expected_datasets(raws, model, endpoint):
    groups = {}
    timeouts = set()
    for raw in raws:
        timeouts.add(raw['inputs']['evaluator'].get('timeout_seconds', 14400))
        for task, config in frozen_tasks(raw, model, endpoint):
            item = dataset(task, config)
            previous = groups.get(item['name'])
            if previous is not None:
                comparable = {k: v for k, v in item.items() if k != 'subset_list'}
                require(comparable == {k: v for k, v in previous.items() if k != 'subset_list'},
                        '同名 benchmark 的配置不同，无法合并到唯一数据集对象')
                require(not set(previous['subset_list']) & set(item['subset_list']), '重复评测不能静默合并')
                previous['subset_list'].extend(item['subset_list'])
            else:
                groups[item['name']] = item
    require(len(timeouts) == 1, '多个基线超时不同，无法无损表达统一 evaluation.timeout_seconds')
    return list(groups.values()), timeouts.pop()


def validate(plan, raws, handoff):
    expected, timeout = expected_datasets(raws, handoff['model']['name'], handoff['endpoint'].rstrip('/'))
    actual = [{**item, 'extra_args': parsed_extra(item['extra_args'])} for item in plan['evaluation']['datasets']]
    require(actual == expected, 'accuracy_plan 未完整保留基线数据集、样本、生成或评分条件')
    require(plan['evaluation']['timeout_seconds'] == timeout, '精度评测超时与原始基线不一致')
