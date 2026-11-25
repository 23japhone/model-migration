"""为新建单卡服务配置空闲的采集适配器，首遍评测没有活动采集会话。"""
from pathlib import Path


def prepare_capture(verification, directory):
    service = verification['service']
    if service['ownership'] != 'owned' or len(verification['npu']['devices']) != 1:
        return
    command = service['command']
    flags = {arg.partition('=')[0].replace('_', '-') for arg in command}
    if '--async-scheduling' in flags:
        return
    version = verification['runtime']['versions'].get('vllm', '').partition('+')[0]
    if version == '0.20.2' and '--no-async-scheduling' not in flags:
        command.append('--no-async-scheduling')
    if directory.resolve() != directory:
        raise ValueError('Token 采集根不能经过符号链接')
    mounts = service['docker']['mounts']
    inherited = [item for item in mounts if directory.is_relative_to(Path(item['source']))]
    if not inherited:
        mounts.append({'source': str(directory), 'target': str(directory), 'read_only': False})
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    verification['worker_capture'] = {'adapter': 'msprobe_pytorch_eager', 'output_root': str(directory)}


def attach(session, task, verification):
    verification['npu']['allocation'] = session.value['steps']['allocation']['receipt']
    if any(key in task.get('accuracy', {}) for key in ('token_alignment', 'tensor_alignment')):
        prepare_capture(verification, session.parent / 'token-worker-capture')
