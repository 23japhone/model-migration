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
