"""可选的 msProbe 准备请求、Eager 服务约束和原生配置。"""
import json
from pathlib import Path
import re

from .aclgraph import config as graph

ADAPTER = 'msprobe_pytorch_eager'
PARALLEL_FLAGS = {'--tensor-parallel-size', '-tp', '--pipeline-parallel-size', '-pp',
                  '--data-parallel-size', '-dp', '--prefill-context-parallel-size', '-pcp',
                  '--decode-context-parallel-size', '-dcp', '--nnodes'}


def validate(request):
    if 'worker_capture' not in request:
        return
    value = request['worker_capture']
    allowed = {'adapter', 'output_root', 'expected_msprobe_version', 'decode_cache'}
    if isinstance(value, dict) and value.get('adapter') == graph.ADAPTER:
        allowed.add('native_dump')
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('worker_capture 须为已知字段的对象')
    if value.get('adapter') not in (ADAPTER, graph.ADAPTER):
        raise ValueError(f'worker_capture.adapter 仅支持 {ADAPTER} 或 {graph.ADAPTER}')
    validate_root(value.get('output_root'))
    if 'decode_cache' in value:
        validate_cache(value['decode_cache'], graph_mode=value.get('adapter') == graph.ADAPTER)
    if 'expected_msprobe_version' in value:
        version = value['expected_msprobe_version']
        if not isinstance(version, str) or not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.!_+-]*', version):
            raise ValueError('expected_msprobe_version 须为准确的安装包版本字符串')


def validate_cache(value, graph_mode=False):
    fields = {'max_snapshot_bytes', 'layer_scope'}
    if graph_mode:
        fields.add('max_device_snapshot_bytes')
    if not isinstance(value, dict) or 'max_snapshot_bytes' not in value or set(value) - fields:
        raise ValueError('decode_cache 需要 max_snapshot_bytes，可选 layer_scope')
    if 'max_device_snapshot_bytes' in value and (type(value['max_device_snapshot_bytes']) != int or value['max_device_snapshot_bytes'] <= 0):
        raise ValueError('图观察设备快照预算须为正整数')
    if value.get('layer_scope', 'all') not in ('all', 'legacy_pair'):
        raise ValueError('layer_scope 仅支持 all 或 legacy_pair')
    if type(value['max_snapshot_bytes']) is not int or value['max_snapshot_bytes'] <= 0:
        raise ValueError('max_snapshot_bytes 须为正整数')


def validate_root(root):
    if not isinstance(root, str) or '\0' in root or not Path(root).is_absolute() or '..' in Path(root).parts:
        raise ValueError('worker_capture.output_root 须为不含 .. 的目标主机绝对路径')


def check_additional_config(value):
    extra = json.loads(value)
    if not isinstance(extra, dict):
        raise ValueError('--additional-config 须为可核对的 JSON 对象')
    if 'dump_config_path' in extra:
        raise ValueError('已有 dump_config_path 与本次配置冲突')


def check_options(command, size=1):
    declared = []
    for index, arg in enumerate(command):
        key, sep, inline = arg.partition('=')
        key = key.replace('_', '-')
        value = inline if sep else (command[index + 1:index + 2] or [''])[0]
        if key in ('-tp', '--tensor-parallel-size'):
            declared.append(value)
        elif key == '--distributed-executor-backend' and size == 2 and value != 'mp':
            raise ValueError('双 rank 采集要求本机 mp executor')
        elif key in PARALLEL_FLAGS and value != '1':
            raise ValueError(f'{size} 个 TP rank 采集要求其他并行维度为 1，冲突参数 {key}')
        check_capture_flag(key, value, size)

    if (declared or ['1']) != [str(size)]:
        raise ValueError('声明的 TP rank 数与物理卡数不一致或重复')


def check_capture_flag(key, value, size):
    if size == 2 and key in ('--enable-expert-parallel', '-ep'):
        raise ValueError('本次双卡采集要求纯张量并行')
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
    if request.get('worker_capture', {}).get('adapter') == graph.ADAPTER:
        graph.settings(request)
    elif '--enforce-eager' not in flags or '--no-enforce-eager' in flags:
        raise ValueError('采集准备要求服务显式启用 Eager')
    devices = request['npu']['devices']
    if len(devices) not in (1, 2) or len(set(devices)) != len(devices):
        raise ValueError('采集仅支持单 rank 或单机双卡 TP=2')
    check_options(command, len(devices))


def native_config(runtime_dump):
    return {'task': 'tensor', 'level': 'mix', 'risk_level': 'ALL', 'dump_enable': False,
            'dump_path': str(runtime_dump), 'async_dump': False, 'rank': [], 'step': [],
            'tensor': {'scope': [], 'list': [], 'data_mode': ['forward']}}
