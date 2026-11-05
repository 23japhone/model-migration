"""管理包外尝试目录和原子证据写入。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import uuid


def safe_text(value):
    text=str(value)
    text=re.sub(r'''(?i)\b(authorization["']?\s*[:=]\s*)["']?(?:bearer|basic)\s+[^\s,;"']+''',r'\1<已脱敏>',text)
    text=re.sub(r'(?i)(https?://)[^/@\s]+@',r'\1<已脱敏>@',text)
    text=re.sub(r'(?i)(https?://[^\s?]+)\?[^\s]+',r'\1?<已脱敏>',text)
    return re.sub(r'''(?i)(password|token|authorization|secret)(["']?\s*[:=]\s*)["']?[^\s,;"']+''',r'\1\2<已脱敏>',text)[:4000]


def write_json(path,value):
    path=Path(path);fd,temporary=tempfile.mkstemp(prefix='.dataset-evidence-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def output_parent(request,work_root,run_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',run_id):raise ValueError('run_id 必须为安全的单层标识')
    root=Path(work_root).expanduser().resolve();target=root/run_id/'vllm-dataset-prepare';assets=Path(request['path'])
    protected = release_root()
    if target.resolve()!=target:raise ValueError('证据输出不能由符号链接重定向')
    boundaries=[protected,assets]
    if 'default_local' in request:
        boundaries.extend(Path(value) for value in (request['default_local']['path'],request['default_local']['evidence']['path']))
    for boundary in boundaries:
        if target.is_relative_to(boundary) or boundary.is_relative_to(target):raise ValueError('证据必须位于技能资源和数据资产之外')
    if request['source']!='local' and assets.is_relative_to(protected):raise ValueError('下载资产必须位于技能资源之外')
    return target


def begin_attempt(parent,request,run_id):
    attempt=parent/uuid.uuid4().hex;attempt.mkdir(parents=True)
    manifest={'schema_version':'1.0','run_id':run_id,'task_id':'vllm-dataset-prepare','inputs':request,'status':'running','conclusions':{'assets':'not_evaluated','evaluation':'not_evaluated'},'evidence':{'manifest':str(attempt/'dataset-manifest.json'),'selection':str(attempt/'sample-selection.json'),'source_snapshot':str(attempt/'source-snapshot.json')},'uncovered':['未安装或运行 AISBench，未计算精度或改动评分规则'],'started_at':datetime.now(timezone.utc).isoformat(),'host':socket.gethostname()}
    return attempt,manifest
