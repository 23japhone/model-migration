"""新尝试中原子发布三文件和内部回执；不覆盖来源和旧产物。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import uuid

from coordination.runtime import sources
from .request import FILES


def payload(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')


def protected(request, configs):
    roots = [Path(item['path']) for key, item in request.items() if key != 'schema_version']
    sources.scan(request, Path('/'), roots)
    roots.extend(Path(value) for value in (configs['benchmark_plan']['tokenizer_path'],
                 configs['deployment']['cann_path'], configs['deployment']['server']['model_path']))
    roots.extend(Path(row['dataset']) for row in configs['accuracy_plan']['evaluation']['datasets'])
    return roots


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def commit(staging, target, received):
    signals = {signal.SIGINT, signal.SIGTERM}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        if received or signals.intersection(signal.sigpending()):
            raise KeyboardInterrupt
        if target.exists():
            raise FileExistsError('交接目标已经存在，不覆盖')
        os.rename(staging, target)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def publish(request, configs, parent, run_id, recheck):
    token = uuid.uuid4().hex
    target = parent/token
    staging = parent/('.pending-'+token)
    roots = protected(request, configs)
    sources.protect(parent, roots)
    parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    received = []
    handlers = {number: signal.signal(number, lambda signum, frame: received.append(signum))
                for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        manifest = stage(request, configs, staging, target, run_id)
        recheck()
        sources.protect(parent, roots)
        commit(staging, target, received)
        return {'status': 'ready', 'manifest': str(target/'export-manifest.json'),
                'deliverables': manifest['deliverables']}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError, KeyboardInterrupt):
        # 只删除本次未发布的暂存目录；原始基线、候选和旧交接不受影响。
        if staging.exists():
            shutil.rmtree(staging)
        failure = {'schema_version': '1.0', 'status': 'failed', 'run_id': run_id, 'inputs': request,
                   'reason': '交接发布失败或取消；未发布正式交接目录'}
        write(parent/(token+'.failed.json'), payload(failure))
        raise
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)


def stage(request, configs, staging, target, run_id):
    files = {}
    for key, relative in FILES.items():
        raw = payload(configs[key])
        write(staging/'deliverables'/relative, raw)
        files[key] = {'path': str(target/'deliverables'/relative), 'sha256': hashlib.sha256(raw).hexdigest()}
    manifest = {'schema_version': '1.0', 'task_id': 'vllm-model-migration-handoff',
                'run_id': run_id, 'status': 'ready', 'inputs': request,
                'finished_at': datetime.now(timezone.utc).isoformat(), 'deliverables': files,
                'uncovered': ['配置交接不证明性能达标、当前服务持续存活或下游真实联调通过']}
    write(staging/'export-manifest.json', payload(manifest))
    return manifest
