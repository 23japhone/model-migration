"""不可覆盖的包外尝试和原子报告。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 含重复字段：'+key)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限 JSON 数字')))


def load(path):
    return decode(Path(path).read_text(encoding='utf-8'))


def bound(reference):
    path = Path(reference['path'])
    if path.resolve() != path or sha256(path) != reference['sha256']:
        raise ValueError('引用文件路径或内容身份不一致：'+path.name)
    result=load(path)
    if not isinstance(result,dict):raise ValueError('引用内容必须为 JSON 对象')
    return result


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def record(request, run_id, action, status):
    return {'schema_version':'1.0','task_id':'verify-agent-plus','run_id':run_id,
                'operation':action,'inputs':request,'status':status,'success':False,
                'conclusions':{'evaluation_validity':'not_evaluated','comparability':'not_evaluated',
                               'precision_target':'not_evaluated'},
                'evidence':{},'uncovered':[],'anomalies':[],'detailed_report':{},'started_at':now(),
                'environment_check':{},'accuracy_evaluation':{},'performance_benchmarks':{},'logs':[]}


def begin(parent, request, run_id, action):
    attempt = parent/uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    manifest=record(request,run_id,action,'running')
    manifest['report_path']=str(attempt/'precision-manifest.json')
    write(manifest['report_path'], manifest)
    return attempt, manifest


def finish(manifest, status):
    manifest.update(status=status, finished_at=now())
    conclusions = manifest['conclusions']
    manifest['success'] = (status=='completed' and conclusions['evaluation_validity']=='pass'
                           and (not manifest['inputs'].get('targets') or conclusions['precision_target']=='pass'))
    manifest['summary'] = ('精度测试完成；目标结论：'+conclusions['precision_target']
                           if status=='completed' else '精度测试未完成，详见诊断及保留证据')
    write(manifest['report_path'], manifest)
    code = 0 if manifest['success'] else (3 if status=='blocked' else 1)
    return code, manifest


def verify_files(files):
    if not isinstance(files,dict):raise ValueError('原始评测文件索引无效')
    for path,expected in files.items():
        if Path(path).resolve()!=Path(path) or sha256(path)!=expected:
            raise ValueError('原始评测证据身份发生变化：'+Path(path).name)
