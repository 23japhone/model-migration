"""确定本次必需资产，任务参数由其公开 CLI 校验。"""
from pathlib import Path
import re

TASKS = ('model', 'image', 'dataset')
PURPOSES = {'assets': (), 'precheck': ('model',), 'deployment': ('model', 'image'),
            'deployment_precision': TASKS}


def normalize_request(value, base):
    if not isinstance(value, dict) or set(value) - {'purpose', 'tasks', 'target', 'version_review'}:
        raise ValueError('阶段请求只能包含契约字段，不能包含凭据')
    purpose = value.get('purpose', 'assets')
    tasks = value.get('tasks')
    if not isinstance(purpose, str) or purpose not in PURPOSES or not isinstance(tasks, dict) or not tasks or set(tasks) - set(TASKS):
        raise ValueError('必须明确有效 purpose 和至少一个准备任务')
    result = {'purpose': purpose, 'tasks': {}, 'version_review': value.get('version_review', [])}
    if 'target' in value:
        result['target'] = normalize_target(value['target'])
    for name in TASKS:
        if name in tasks:
            result['tasks'][name] = normalize_task(tasks[name], base, name in PURPOSES[purpose])
        elif name in PURPOSES[purpose]:
            result['tasks'][name] = {'required': True, 'error': '本次范围缺少必需任务请求'}
    if not any(t['required'] for t in result['tasks'].values()):
        raise ValueError('至少一个任务必须是必需资产')
    if not isinstance(result['version_review'], list):
        raise ValueError('version_review 必须为资料复核记录列表')
    return result


def normalize_task(value, base, forced):
    required = True
    try:
        if not isinstance(value, dict) or set(value) - {'request', 'required', 'python', 'timeout_seconds'}:
            raise ValueError('任务条目只接受 request、required、python、timeout_seconds')
        required = value.get('required', True)
        if type(required) is not bool:
            raise ValueError('required 必须为布尔值')
        path = value.get('request')
        if not isinstance(path, str) or not path.strip() or '\0' in path:
            raise ValueError('任务 request 必须指向实际 JSON 文件')
        result = {'request': str((base / Path(path).expanduser()).resolve()), 'required': forced or required,
                  'timeout_seconds': value.get('timeout_seconds', 3600)}
        execution_options(value, result)
        return result
    except ValueError as error:
        return {'required': forced or bool(required), 'error': str(error)}


def execution_options(value, result):
    seconds = result['timeout_seconds']
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise ValueError('任务 timeout_seconds 必须为 1—86400 的整数')
    if 'python' in value:
        python = value['python']
        if not isinstance(python, str) or not Path(python).is_absolute() or '\0' in python:
            raise ValueError('python 必须为已安装解释器的绝对路径')
        result['python'] = python


def normalize_target(value):
    fields = {'backend', 'vllm', 'vllm_ascend', 'platform', 'hardware'}
    if not isinstance(value, dict) or set(value) != fields or value['backend'] != 'vllm-ascend':
        raise ValueError('target 必须明确 vllm-ascend、两个版本、platform 和 hardware')
    for name in ('vllm', 'vllm_ascend'):
        if not isinstance(value[name], str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+_-]*', value[name]):
            raise ValueError('target 必须固定版本，不能使用 latest')
    hardware = value['hardware']
    if not isinstance(hardware, dict) or set(hardware) != {'name', 'family'} or any(
            not isinstance(x, str) or not x.strip() for x in hardware.values()):
        raise ValueError('hardware 必须记录实际芯片名称及产品系列')
    if not re.fullmatch(r'linux/(arm64|amd64)(/v[0-9]+)?', str(value['platform'])):
        raise ValueError('target 必须采用明确的 Linux 平台')
    return value
