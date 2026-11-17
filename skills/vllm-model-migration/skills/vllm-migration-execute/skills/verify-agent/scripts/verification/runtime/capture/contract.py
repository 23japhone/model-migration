"""可选的 msProbe 准备请求、Eager 服务约束和原生配置。"""
import json
from pathlib import Path
import re

ADAPTER = 'msprobe_pytorch_eager'
PARALLEL_FLAGS = {'--tensor-parallel-size', '-tp', '--pipeline-parallel-size', '-pp',
                  '--data-parallel-size', '-dp', '--prefill-context-parallel-size', '-pcp',
                  '--decode-context-parallel-size', '-dcp', '--nnodes'}


def validate(request):
    if 'worker_capture' not in request:
        return
    value = request['worker_capture']
    if not isinstance(value, dict) or set(value) - {'adapter', 'output_root', 'expected_msprobe_version'}:
        raise ValueError('worker_capture 须为已知字段的对象')
    if value.get('adapter') != ADAPTER:
        raise ValueError('worker_capture.adapter 须为 ' + ADAPTER)
    validate_root(value.get('output_root'))
    if 'expected_msprobe_version' in value:
        version = value['expected_msprobe_version']
        if not isinstance(version, str) or not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.!_+-]*', version):
            raise ValueError('expected_msprobe_version 须为准确的安装包版本字符串')


def validate_root(root):
    if not isinstance(root, str) or '\0' in root or not Path(root).is_absolute() or '..' in Path(root).parts:
        raise ValueError('worker_capture.output_root 须为不含 .. 的目标主机绝对路径')


def check_additional_config(value):
    extra = json.loads(value)
    if not isinstance(extra, dict):
        raise ValueError('--additional-config 须为可核对的 JSON 对象')
    if 'dump_config_path' in extra:
        raise ValueError('已有 dump_config_path 与本次配置冲突')


def check_options(command):
    for index, arg in enumerate(command):
        key, sep, inline = arg.partition('=')
        key = key.replace('_', '-')
        value = inline if sep else (command[index + 1:index + 2] or [''])[0]
        if key in PARALLEL_FLAGS and value != '1':
            raise ValueError('采集准备仅支持单 rank，冲突参数 ' + key)
        if key == '--config':
            raise ValueError('外部 --config 的执行模式和采集设置未核实')
        if key == '--additional-config':
            check_additional_config(value)
        if key.startswith('--additional-config.dump-config-path') or key == '--dump-config-path':
            raise ValueError('已有 dump_config_path 与本次配置冲突')


def check_service(request):
    service = request['service']
    if service['ownership'] != 'owned':
        raise ValueError('borrowed 服务不支持准备新的 worker 采集配置')
    command = service['command']
    flags = [arg.replace('_', '-') for arg in command]
    if '--enforce-eager' not in flags or '--no-enforce-eager' in flags:
        raise ValueError('采集准备要求服务显式启用 Eager')
    if len(request['npu']['devices']) != 1:
        raise ValueError('采集准备要求单 rank、单物理 NPU')
    check_options(command)


def native_config(runtime_dump):
    return {'task': 'tensor', 'level': 'mix', 'risk_level': 'ALL', 'dump_enable': False,
            'dump_path': str(runtime_dump), 'async_dump': False, 'rank': [], 'step': [],
            'tensor': {'scope': [], 'list': [], 'data_mode': ['forward']}}
