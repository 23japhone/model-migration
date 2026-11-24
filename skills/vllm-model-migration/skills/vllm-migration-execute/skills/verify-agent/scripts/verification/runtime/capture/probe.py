"""由目标 Python 执行。检查 PyTorch 入口、配置字节和写入能力。"""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys


def checked_dump(payload):
    from msprobe.pytorch.dump.pt_config import parse_json_config
    common, tensor = parse_json_config(payload['config'], 'tensor')
    if (common.task, common.level, common.risk_level, common.dump_enable) != ('tensor', 'mix', 'ALL', False):
        raise ValueError('目标 msProbe 未解析所需的 tensor/mix、risk_level 或关闭采集设置')
    if tensor.data_mode != ['forward'] or common.dump_path != payload['dump_path']:
        raise ValueError('目标 msProbe 未解析所需的前向范围或输出路径')
    return Path(common.dump_path)


def inspect_cache():
    try:
        from mindie_debugger.precision import MemoryWatchpoint
        from msprobe.core.dump.hook_manager import BaseHookManager
        version = metadata.version('mindie-debugger')
        if version != '0.1.4' or not all(callable(getattr(MemoryWatchpoint, name)) for name in ('watch', 'check')) or not hasattr(BaseHookManager, 'inner_switch'):
            raise ValueError('需要 mindie-debugger 0.1.4 和兼容的 msProbe 线程内采集开关')
        return {'status': 'available', 'version': version}
    except Exception as error:
        return {'status': 'unavailable', 'reason': str(error)}


def inspect_graph(payload, config):
    from importlib.util import find_spec
    expected = Path(payload['dump_path']) / 'native'
    if config['task'] != 'tensor' or Path(config['dump_path']) != expected:
        raise ValueError('图采集需要独立的原生张量目录')
    if payload['graph']['native']:
        from msprobe.pytorch import AclGraphDumper
        if not callable(AclGraphDumper):
            raise ValueError('目标 msProbe 缺少 AclGraphDumper 或 aclgraph_dump 扩展')
        from msprobe.pytorch.aclgraph_dump import acl_tensor_save
    names = ['vllm_ascend.compilation.acl_graph', 'vllm_ascend.worker.model_runner_v1',
             'vllm_ascend.attention.attention_v1', 'vllm_ascend.ops.gdn']
    if payload['graph']['native']:
        names.extend([AclGraphDumper.__module__, acl_tensor_save.__module__])
        from msprobe.lib import aclgraph_dump_ext
        names.append(aclgraph_dump_ext.__name__)
    if payload['decode_cache']:
        from mindie_debugger.aclgraph import ACLGraphAddressTracker
        names.append('mindie_debugger.aclgraph')
    return {name: hashlib.sha256(Path(find_spec(name).origin).read_bytes()).hexdigest() for name in names}


def inspect(payload):
    result = {'executable': sys.executable, 'executable_realpath': str(Path(sys.executable).resolve())}
    try:
        result['msprobe_version'] = metadata.version('mindstudio-probe')
        expected = payload.get('expected_msprobe_version')
        if expected is not None and result['msprobe_version'] != expected:
            raise ValueError('msProbe 版本不匹配，要求 ' + expected + '，实际 ' + result['msprobe_version'])
        from msprobe.pytorch import PrecisionDebugger
        if not callable(PrecisionDebugger):
            raise ValueError('缺少可用的 msprobe.pytorch.PrecisionDebugger 入口')
        for key in ('WORLD_SIZE', 'LOCAL_WORLD_SIZE', 'VLLM_DP_SIZE'):
            allowed = ['1'] if key == 'VLLM_DP_SIZE' else ['1', str(payload.get('world_size', 1))]
            if os.environ.get(key, '1') not in allowed:
                raise ValueError('目标环境不是单 rank：' + key)
        if os.environ.get('MSPROBE_CONFIG_PATH'):
            raise ValueError('目标环境已有 MSPROBE_CONFIG_PATH')
        data = Path(payload['config']).read_bytes()
        result['config_sha256'] = hashlib.sha256(data).hexdigest()
        if result['config_sha256'] != payload['config_sha256']:
            raise ValueError('目标配置 SHA256 与宿主机不一致')
        if payload.get('graph'):
            dump = Path(payload['dump_path'])
            result['graph'] = inspect_graph(payload, json.loads(data))
            result['graph_versions'] = {name: metadata.version(name) for name in (
                'torch', 'torch-npu', 'vllm', 'vllm-ascend')}
        else:
            dump = checked_dump(payload)
        if payload.get('decode_cache'):
            result['decode_cache'] = inspect_cache()
        with (dump / payload['marker']).open('x', encoding='ascii') as stream:
            stream.write(payload['challenge'])
    except Exception as error:
        # 包导入（含原生依赖）可能有自己的异常类型。取消信号不在此处处理。
        result['error'] = f'{type(error).__name__}: {error}'
    return result


if __name__ == '__main__':
    print('VLLM_CAPTURE_JSON=' + json.dumps(inspect(json.load(sys.stdin))))
