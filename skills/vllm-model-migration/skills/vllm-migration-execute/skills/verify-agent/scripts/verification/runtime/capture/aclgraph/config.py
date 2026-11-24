"""单卡图采集部署参数。模式在编图之前确定。"""
import json

ADAPTER = 'msprobe_pytorch_aclgraph'


def option(command, name):
    values = []
    for i, arg in enumerate(command):
        key, equal, value = arg.partition('=')
        if key.replace('_', '-') == name:
            values.append(value if equal else next(iter(command[i+1:i+2]), ''))
    if len(values) != 1:
        raise ValueError('需要唯一的服务参数 ' + name)
    return values[0]


def settings(request):
    command = request['service']['command']
    flags = {arg.split('=')[0].replace('_', '-') for arg in command}
    required = {'--no-async-scheduling', '--no-enable-prefix-caching'}
    if (len(request['npu']['devices']) != 1 or '--enforce-eager' in flags or not required <= flags
            or flags & {'--async-scheduling', '--enable-prefix-caching', '--speculative-config'}
            or option(command, '--max-num-seqs') != '1' or option(command, '--dtype') not in ('bfloat16', 'bf16')):
        raise ValueError('ACLGraph 采集要求单卡、BF16、同步调度、单请求和冷前缀')
    config = json.loads(option(command, '--compilation-config'))
    if not isinstance(config, dict) or config.get('cudagraph_mode') not in ('FULL', 'PIECEWISE'):
        raise ValueError('ACLGraph 需要明确的 FULL 或 PIECEWISE')
    capture = request['worker_capture']
    observer = 'decode_cache' in capture
    if observer and capture['decode_cache'].get('layer_scope', 'all') != 'all':
        raise ValueError('图模式需要全层观察')
    native = capture.get('native_dump', True)
    if type(native) != bool or observer and not native:
        raise ValueError('图缓存观测须与原生 msProbe 联合部署')
    return {'requested_mode': config['cudagraph_mode'], 'native': native, 'observe_cache': observer}


def native_config(path):
    return {'task': 'tensor', 'level': 'mix', 'rank': [], 'dump_path': str(path),
            'dump_enable': False, 'tensor': {'list': []}}
