"""为本次服务复制并封存适配器，在受支持的 Python/vLLM 入口加载它。"""
from pathlib import Path
import shutil

from ...evidence import sha256


def stage(receipt):
    config = receipt['config']
    folder = Path(config['path']).parent / 'adapters'
    source = Path(__file__).with_name('adapters')
    folder.mkdir()
    files = {}
    for path in sorted(source.glob('*.py')):
        dest = folder / path.name
        shutil.copyfile(path, dest)
        files[path.name] = sha256(dest)
    receipt['adapter_files'] = files


def command(request):
    original = request['service']['command']
    receipt = request.get('_capture_preparation', {})
    if receipt.get('status') != 'prepared':
        return original
    root = Path(receipt['config']['runtime_path']).parent
    shim = [request['runtime']['python'], str(root / 'adapters' / 'bootstrap.py')]
    if Path(original[0]).name == 'vllm' and original[1:2] == ['serve']:
        return [*shim, 'cli', *original[1:]]
    for index in range(1, len(original) - 1):
        if original[index:index + 2] == ['-m', 'vllm.entrypoints.openai.api_server'] and all(
                flag in ('-B', '-u') for flag in original[1:index]):
            return [shim[0], *original[1:index], shim[1], 'module', *original[index + 2:]]
    return original
