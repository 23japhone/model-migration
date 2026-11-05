"""候选部署参数与已验证 vLLM argv、环境和模型身份的一致性。"""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from coordination.runtime import sources
from . import runtime_environment as runtime_evidence
from .contracts import require
from device_contract import effective_runtime
from allocation_contract import configuration as allocation_configuration


def arguments(argv):
    require(isinstance(argv, list) and argv and all(isinstance(v, str) for v in argv),
            '缺少本次已验证的启动 argv；不能从模型名猜测部署参数')
    if re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(argv[0]).name):
        offset = 2 if argv[1:2] == ['-B'] else 1
        if argv[offset:offset+2] == ['-m', 'vllm.entrypoints.openai.api_server']:
            return argv[offset+2:]
    if len(argv) >= 3 and Path(argv[0]).name == 'vllm' and argv[1] == 'serve':
        return ['--model', argv[2], *argv[3:]]
    raise ValueError('启动包装命令无法无损映射；需要直接 vLLM 公开启动 argv')


def options(argv):
    result = {}
    index = 0
    while index < len(argv):
        flag, equal, first = argv[index].partition('=')
        require(re.fullmatch(r'--[a-z][a-z0-9-]*', flag) is not None, '启动选项无法无损表达')
        require(flag not in result, '重复启动选项无法用 server 对象无损表达')
        values = [first] if equal else []
        index += 1
        while index < len(argv) and not argv[index].startswith('--'):
            values.append(argv[index])
            index += 1
        result[flag] = values
    return result


def encoded(value):
    if type(value) is bool:
        require(value is True, '显式布尔 false 会被下游省略；须使用真实选项或字符串值')
        return []
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)]
    if isinstance(value, list):
        require(bool(value) and all(type(item) in (str, int, float) for item in value), '多值启动参数类型无效')
        return [str(item) for item in value]
    require(type(value) in (str, int, float), '启动参数不能为 null 或不支持的类型')
    return [str(value)]


def comparable(values):
    if len(values) == 1 and values[0].lstrip().startswith(('{', '[')):
        try:
            return json.loads(values[0], object_pairs_hook=sources.pairs, parse_constant=sources.invalid_constant)
        except ValueError:
            pass
    return values


def server_options(server):
    require('model' not in server, '模型路径统一使用 server.model_path')
    result = {}
    for key, value in server.items():
        require(re.fullmatch(r'[a-z][a-z0-9_]*', key) is not None, 'server 选项名无效')
        flag = '--model' if key == 'model_path' else '--'+key.replace('_', '-')
        result[flag] = encoded(value)
    return result


def configuration(original):
    """比较声明部署条件；容器名称、PID、时间和验收执行策略不定义配置身份。"""
    model, runtime, service = (original[key] for key in ('model', 'runtime', 'service'))
    require(isinstance(model, dict) and bool(model.get('files')), '缺少固定的模型文件身份')
    require(isinstance(runtime, dict) and bool(runtime), '缺少声明运行环境')
    command = options(arguments(service.get('command')))
    docker = {key: value for key, value in service.get('docker', {}).items() if key != 'name'}
    value = {'model': model, 'runtime': runtime, 'endpoint': service['endpoint'].rstrip('/'),
             'server': {key: comparable(values) for key, values in command.items()},
             'docker': docker, 'npu': allocation_configuration(original),
             'image_id': docker.get('image_id', service.get('identity', {}).get('image_id'))}
    # JSON 比较保留布尔/整数区别，忽略对象键顺序；原始声明和回执不改写。
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate(config, benchmark, accuracy, original, handoff, runtime_environment=None):
    service = original['service']
    require(not original.get('api_key_env'), '当前下游接口未提供安全认证注入，不能交接认证服务')
    actual = options(arguments(service.get('command')))
    candidate = server_options(config['server'])
    require({k: comparable(v) for k, v in actual.items()} == {k: comparable(v) for k, v in candidate.items()},
            'server 与本次已验证启动 argv 不一致，不允许遗漏或添加参数')
    require(config['env_variables'] == effective_runtime(handoff, original)['env'], '环境变量与已验证有效部署不一致')
    runtime_evidence.validate(config['cann_path'], original, handoff, runtime_environment)
    name = handoff['model']['name']
    require(config['server']['model_path'] == handoff['model']['runtime_root'], '容器内模型路径不一致')
    require(config['server']['served_model_name'] == name, 'served_model_name 不一致')
    endpoint = handoff['endpoint'].rstrip('/')
    require(config['server']['port'] == urlsplit(endpoint).port, '部署端口与服务地址不一致')
    for value in (benchmark, accuracy):
        require(value['service']['base_url'].rstrip('/') == endpoint and value['service']['model'] == name,
                '三个配置的服务模型或访问地址不一致')
    require(accuracy['evaluation']['model_path'] == name,
            'evaluation.model_path 须为实际服务模型名，避免下游 --model 发出其他模型 ID')
    tokenizer = actual.get('--tokenizer', [handoff['model']['runtime_root']])
    allowed = {handoff['model']['root'], handoff['model']['runtime_root']} if '--tokenizer' not in actual else set(tokenizer)
    require(benchmark['tokenizer_path'] in allowed, 'tokenizer_path 不属于已验证模型或显式 tokenizer')
    verify_tokenizer(benchmark['tokenizer_path'], handoff['model'])


def verify_tokenizer(path, model):
    if path not in (model['root'], model['runtime_root']):
        raise ValueError('独立 tokenizer 缺少模型文件摘要绑定，暂不能交接')
    expected = model['files'].get('tokenizer_config.json')
    require(expected is not None, '模型证据未固定 tokenizer_config.json 摘要')
    from model_cache import checked
    require(sources.ref(checked(path, 'tokenizer_config.json'))['sha256'] == expected, 'tokenizer 内容已变化')
