"""在目标解释器中只读采集版本、文件和原生NPU；通过stdin接收JSON。"""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys


def inspect_files(files):
    result = {}
    for name, expected in files.items():
        path = Path(name)
        digest = hashlib.sha256()
        if path.is_symlink():raise ValueError('载体文件为符号链接')
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):digest.update(block)
        result[name] = {'sha256': digest.hexdigest(), 'match': digest.hexdigest() == expected}
    return result


def main():
    request = json.load(sys.stdin)
    import torch
    import torch_npu
    result = {'executable': sys.executable, 'executable_realpath': str(Path(sys.executable).resolve()),
              'versions': {name: metadata.version(name) for name in request['versions']},
              'files': inspect_files(request['files']), 'npu_count': torch.npu.device_count(),
              'device_names': [torch.npu.get_device_name(i) for i in range(torch.npu.device_count())]}
    print('VLLM_VERIFY_JSON=' + json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':main()
