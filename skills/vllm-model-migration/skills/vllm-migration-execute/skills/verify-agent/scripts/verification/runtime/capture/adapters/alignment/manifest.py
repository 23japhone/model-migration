"""两端共用的 msProbe 定向调用原始索引。文件引用相对于 bundle 所在目录。"""
import hashlib
from importlib import metadata
from ..state import write


def runtime_versions():
    return {key: metadata.version(package) for key, package in
            (('torch', 'torch'), ('vllm', 'vllm'), ('msprobe', 'mindstudio-probe'))}


def write_native_index(folder, hardware, plan_sha256, forward_id, execution, rows, native_frame=None):
    path = folder / 'tensor-native-index.json'
    native = {'format': 'tensor-native-index-v1', 'hardware': hardware, 'plan_sha256': plan_sha256,
              'forward_id': forward_id, 'rank': execution['rank'], 'execution': execution, 'tensors': rows}
    if native_frame is not None:
        native['native_frame'] = native_frame
    write(path, native)
    return {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
