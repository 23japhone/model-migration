"""通过公开 verify 入口持有本次租约，并封存实际评测请求。"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import uuid

from ..contract import release_skill
from ..evidence import decode, write
from ..quality.protocol import ref


def invoke(root, request):
    label = request['operation'] + '-' + uuid.uuid4().hex
    path = root / (label + '.json')
    write(path, request)
    cli = release_skill('verify-agent') / 'scripts/verify.py'
    args = [sys.executable, '-B', str(cli), '--action', 'capture-session', '--request', str(path),
            '--work-root', str(root / 'verify'), '--run-id', 'token-capture']
    with (root / (label + '.stderr')).open('w') as log:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=log, timeout=600)
    if not result.stdout.strip():
        raise ValueError(f"预测采集 {request['operation']} 未交付回执，详见 {label}.stderr")
    response = decode(result.stdout)
    if result.returncode or response.get('status') != 'completed':
        raise ValueError('预测采集 ' + request['operation'] + ' 失败：' + str(response))
    return response


@contextmanager
def session(root, deployment):
    start = invoke(root, {'operation': 'begin', 'source': deployment, 'owner_pid': os.getpid()})
    source = start['session']
    try:
        yield source
    finally:
        invoke(root, {'operation': 'end', 'source': source})


def post_pair(index, folder):
    rows = [r for r in index['requests'] if r.get('method') == 'POST']
    if len(rows) != 1 or index['errors'] or rows[0].get('status') != 200 or rows[0]['route'] != '/v1/chat/completions':
        raise ValueError('单题采集要求一次成功的正式推理请求')
    files = rows[0]['files']
    names = [Path(p).parent for p in files if Path(p).name == 'request.bin']
    if len(names) != 1:
        raise ValueError('模型请求字节缺失')
    path = folder / names[0]
    pair = {k: ref(path / (k + '.bin')) for k in ('request', 'response')}
    if any(files.get(str(Path(item['path']).relative_to(folder))) != item['sha256'] for item in pair.values()):
        raise ValueError('模型 HTTP 原件摘要变化')
    return pair
