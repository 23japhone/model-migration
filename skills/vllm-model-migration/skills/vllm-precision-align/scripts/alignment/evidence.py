"""固定JSON身份、独立尝试和终态台账交接。"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


class EvidenceError(OSError):
    """无法保存证据时停止后续外部效果。"""


def safe(value):
    text=str(value)
    text=re.sub(r'(?i)((?:password|token|secret|api[_-]?key|authorization)\s*[=:]\s*)[^\r\n,;]+',r'\1[已隐藏]',text)
    return re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@',r'\1',text)


def digest(path):
    path=Path(path)
    if path.resolve()!=path:raise ValueError('来源不能经符号链接重定向')
    result=hashlib.sha256()
    with path.open('rb') as stream:
        before=os.fstat(stream.fileno())
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
        after=os.fstat(stream.fileno())
    stamps=[(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns) for s in (before,after,path.stat())]
    if stamps[0]!=stamps[1] or stamps[1]!=stamps[2]:raise ValueError('来源读取期间变化')
    return result.hexdigest()


def pairs(items):
    value={}
    for key,item in items:
        if key in value:raise ValueError('JSON含重复字段')
        value[key]=item
    return value


def invalid_constant(value):raise ValueError('JSON不得含非有限数字')


def parse(text):
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=invalid_constant)
    if not isinstance(value,dict):raise ValueError('JSON根必须为对象')
    return value


def read(path):
    path=Path(path)
    if path.stat().st_size>64*1024*1024:raise ValueError('JSON超过64MiB')
    before=digest(path);value=parse(path.read_text(encoding='utf-8-sig'))
    if before!=digest(path):raise ValueError('JSON读取期间变化')
    return value


def ref(path):return {'path':str(path),'sha256':digest(path)}


def bound(reference):
    value=read(reference['path'])
    if digest(reference['path'])!=reference['sha256']:raise ValueError('来源SHA256不符')
    return value


def write(path,value):
    path=Path(path);temporary=None
    try:
        fd,temporary=tempfile.mkstemp(prefix='.precision-stage-',dir=path.parent)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
        os.replace(temporary,path)
    except OSError as error:raise EvidenceError('证据保存失败：'+safe(error)) from error
    finally:
        if temporary and os.path.exists(temporary):os.unlink(temporary)


def now():return datetime.now(timezone.utc).isoformat()


def save(attempt,value):
    write(attempt/'task-ledger.json',value['tasks'])
    write(attempt/'precision-stage-manifest.json',value)


def begin(request,parent,run_id,action):
    attempt=parent/uuid.uuid4().hex
    try:attempt.mkdir(parents=True,exist_ok=False)
    except OSError as error:raise EvidenceError('尝试目录创建失败：'+safe(error)) from error
    tasks={group:{key:{'status':'not_run'} for key in request.get(group,{})} for group in ('datasets','deployments')}
    tasks['evaluations']={row['id']:{'status':'not_run'} for row in request['evaluations']}
    tasks['diagnoses']={row['id']:{'status':'not_run'} for row in request.get('diagnoses',[])}
    value={'schema_version':'1.0','task_id':'vllm-precision-align','run_id':run_id,'operation':action,
           'inputs':request,'status':'running','success':False,'started_at':now(),'tasks':tasks,'results':{},
           'resources':[],'errors':[], 'conclusions':{key:'not_evaluated' for key in ('precision_testing','evaluation_validity','comparability','precision_target','demarcation')},
           'evidence':{key:str(attempt/name) for key,name in (('manifest','precision-stage-manifest.json'),('ledger','task-ledger.json'),('handoff','precision-stage-handoff.json'))},
           'uncovered':['本阶段不诊断、调参、自动重测或执行性能验收'],'next_action':'running'}
    write(attempt/'stage.request.json',request);save(attempt,value)
    return attempt,value


def publish(attempt,value):
    value['finished_at']=now()
    write(attempt/'task-ledger.json',value['tasks'])
    handoff={'schema_version':'1.0','task_id':'vllm-precision-align','run_id':value['run_id'],
             'status':value['status'],'ledger':ref(attempt/'task-ledger.json'),'tasks':value['tasks'],
             'results':value['results'],'resources':value['resources'],'conclusions':value['conclusions'],'uncovered':value['uncovered']}
    write(attempt/'precision-stage-handoff.json',handoff)
    value['handoff']=ref(attempt/'precision-stage-handoff.json')
    write(attempt/'precision-stage-manifest.json',value)
    code=0 if value['success'] else (3 if value['status']=='blocked' else 1)
    return code,{'manifest':value['evidence']['manifest'],'status':value['status'],'conclusions':value['conclusions'],'next_action':value['next_action']}
