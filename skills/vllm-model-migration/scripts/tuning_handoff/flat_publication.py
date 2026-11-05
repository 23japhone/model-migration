"""客户三文件整体发布；导出回执独立保存，失败不覆盖既有交付。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from coordination.runtime import sources
from .contracts import require
from .publication import payload, protected, write

FILES = {key: key + '.json' for key in ('benchmark_plan', 'accuracy_plan', 'deployment')}


def existing(target, blobs):
    if target.is_symlink() or not target.is_dir():
        raise ValueError('已有交付路径类型无效')
    require({p.name for p in target.iterdir()} == set(FILES.values()), '已有交付目录内容不同，不覆盖')
    for key, relative in FILES.items():
        path = target / relative
        require(not path.is_symlink() and path.read_bytes() == blobs[key], '已有交付文件不同，不覆盖')


def publish(request, configs, run_root, run_id, recheck):
    run_root = Path(run_root)
    target = run_root / 'deliverables'
    roots = protected(request, configs)
    sources.protect(target, roots)
    token = uuid.uuid4().hex
    evidence = run_root / 'customer-workflow' / 'exports' / token
    sources.protect(evidence, roots)
    evidence.mkdir(parents=True)
    staging = run_root / ('.deliverables-' + token)
    blobs = {key: payload(configs[key]) for key in FILES}
    files = {key: {'path': str(target / relative), 'sha256': hashlib.sha256(blobs[key]).hexdigest()}
             for key, relative in FILES.items()}
    manifest = {'schema_version': '1.0', 'task_id': 'vllm-model-migration-handoff', 'run_id': run_id,
                'status': 'pending', 'inputs': request, 'deliverables': files, 'layout': 'flat',
                'uncovered': ['交付配置不证明性能目标达标或当前服务持续存活']}
    write(evidence / 'publication-intent.json', payload(manifest))
    try:
        staging.mkdir()
        for key, relative in FILES.items():
            write(staging / relative, blobs[key])
        recheck()
        sources.protect(target, roots)
        if target.exists():
            existing(target, blobs)
            shutil.rmtree(staging)
        else:
            os.rename(staging, target)
        # 目录已整体发布后才发布 ready；若写回执失败，不能宣告完成。
        manifest['status'] = 'ready'
        write(evidence / 'export-manifest.json', payload(manifest))
        return {'status': 'ready', 'manifest': str(evidence / 'export-manifest.json'), 'deliverables': files}
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
