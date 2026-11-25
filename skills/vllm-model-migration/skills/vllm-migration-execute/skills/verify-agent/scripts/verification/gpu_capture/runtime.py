"""GPU worker 的运行时身份、独占探针和按请求验证的重定向条件。"""
import hashlib
import json
from pathlib import Path

from ..inputs import file_inventory
from ..runtime.capture.adapters.alignment.manifest import runtime_versions
from ..runtime.capture.adapters.alignment.worker import check_observer
from ..runtime.capture.contract import native_config
from .record import write_json


def runtime_identity(runner, plan, files):
    if not isinstance(files, dict) or not files:
        raise ValueError('GPU 运行时需提供冻结的相对文件名及 SHA256 清单')
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if digest != plan['model_identity']:
        raise ValueError('GPU 运行时权重清单与首轮模型身份不同')
    root = Path(runner.model_config.model)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('GPU worker 需要可核实的本地运行时模型路径')
    exts = ('.safetensors', '.bin', '.pt', '.pth')
    weights = {key for key in files if key.endswith(exts)}
    actual = {str(path.relative_to(root)) for path in root.rglob('*') if path.suffix in exts}
    if not weights or actual != weights:
        raise ValueError('GPU 原始权重文件集合与首轮运行时不同')
    inspected = file_inventory(root, files, model_cache=True)
    if inspected['errors']:
        raise ValueError('GPU 资产复核未通过：' + ', '.join(inspected['errors'][:2]))
    versions = runtime_versions()
    if versions != plan.get('runtime_versions', {}).get('gpu'):
        raise ValueError('GPU 运行时的模型清单或软件版本与冻结范围不同')
    return versions


def initialize(runner, folder):
    from msprobe.pytorch import PrecisionDebugger
    owned = getattr(runner, '_runtime_tensor_precision_debugger', None)
    check_observer(owned)
    if owned is None:
        path = folder / 'capture-config.json'
        write_json(path, {**native_config(folder / 'msprobe'), 'dump_enable': True})
        owned = PrecisionDebugger(str(path))
        check_observer(owned)
        runner._runtime_tensor_precision_debugger = owned
    return owned
