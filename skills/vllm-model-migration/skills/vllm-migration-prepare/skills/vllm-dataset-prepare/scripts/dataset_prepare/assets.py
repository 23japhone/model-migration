"""核对配方所需完整布局，记录内容身份和原始样本位置。"""
import hashlib
import json
from pathlib import Path
import random

from .formats import row_count
from .request import RECIPES,relative


def file_hash(path):
    hashed=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):hashed.update(block)
    return hashed.hexdigest()


def state(path):
    info=path.stat();return info.st_size,info.st_mtime_ns,info.st_ino


def checked_files(root):
    items=[root] if root.is_file() else sorted(root.rglob('*'))
    for path in items:
        if path.is_symlink():raise ValueError('数据集目录不能包含符号链接')
    return [path for path in items if path.is_file()]


def dataset_root(path,recipe):
    root=Path(path)
    if not root.exists():raise ValueError('数据集路径不存在')
    checked_files(root)
    if recipe=='generic':return root
    if not root.is_dir():raise ValueError('预置配方必须提供数据集目录')
    if recipe=='gsm8k':
        candidates=[p.parent for p in root.rglob('test.jsonl') if (p.parent/'train.jsonl').is_file()]
    else:
        splits=RECIPES[recipe]['splits']
        candidates=[p.parent for p in root.rglob('dev') if p.is_dir() and all((p.parent/s).is_dir() for s in splits)]
    if len(candidates)!=1:raise ValueError('配方所需配套 split 缺失或数据集目录不唯一')
    return candidates[0]


def layout(root,request):
    recipe=request['recipe'];split=request['split']
    if recipe=='generic':
        return generic_layout(root,request)
    if recipe=='gsm8k':return root,[(root/(s+'.jsonl'),s,None) for s in RECIPES[recipe]['splits']]
    names=sorted(p.stem[:-4] for p in (root/'dev').glob('*_dev.csv'))
    if not names or set(request['subjects'])-set(names):raise ValueError('请求科目缺失，或没有找到配方 CSV')
    files=[(root/s/(name+'_'+s+'.csv'),s,name) for name in names for s in RECIPES[recipe]['splits']]
    return root,files


def generic_layout(root,request):
    if root.is_file():
        return root.parent,[(root,request['split'],None)]
    paths=[root/relative(x) for x in request['files']] if request['files'] else [
        p for p in checked_files(root) if p.suffix=='.'+request['format']
        and p.relative_to(root).as_posix() not in request.get('supporting_files',[])]
    return root,[(p,request['split'],None) for p in sorted(paths)]


def selection(records,request):
    candidates=[]
    for row in records:
        if row['split']!=request['split']:continue
        if request['selection'].get('file',row['path'])!=row['path']:continue
        if request['subjects'] and row['subject'] not in request['subjects']:continue
        candidates.extend({'file':row['path'],'row_index':i,'split':row['split'],'subject':row['subject']} for i in range(row['rows']))
    spec=request['selection'];offset=spec['offset']
    if offset>=len(candidates):raise ValueError('样本选择 offset 超出当前 split 范围')
    candidates=candidates[offset:];limit=min(spec.get('limit',len(candidates)),len(candidates))
    indexes=range(limit) if 'seed' not in spec else sorted(random.Random(spec['seed']).sample(range(len(candidates)),limit))
    chosen=[candidates[i] for i in indexes]
    identity=hashlib.sha256(json.dumps(chosen,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'split':request['split'],'subjects':request['subjects'],'parameters':spec,'count':len(chosen),'records':chosen,'sha256':identity}


def directory_metadata(root,recipe):
    path=root/'README.md'
    return [(path,None,None)] if recipe=='ceval' and path.is_file() else []


def complete_entries(root,base,entries,request):
    entries.extend(directory_metadata(root,request['recipe']))
    listed={p for p,_,_ in entries}
    for name in request.get('supporting_files',[]):
        if base/name not in listed:
            entries.append((base/name,None,None))
    return entries


def inspect_dataset(request,path=None):
    root=dataset_root(path or request['path'],request['recipe']);base,entries=layout(root,request)
    if not entries:raise ValueError('未找到本次格式的必需数据文件')
    entries=complete_entries(root,base,entries,request)
    roster=checked_files(root);before={str(p):state(p) for p in roster};rows=[]
    for path,split,subject in entries:
        if not path.is_file() or path.is_symlink():raise ValueError('配方所需数据文件缺失或不安全')
        rows.append({'path':path.relative_to(base).as_posix(),'size':path.stat().st_size,'sha256':file_hash(path),'rows':row_count(path,request,split) if split else 0,'split':split,'subject':subject})
    content=[{k:row[k] for k in ('path','size','sha256')} for row in sorted(rows,key=lambda r:r['path'])]
    identity={'format':'vllm-dataset-file-list-v1','sha256':hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    if request.get('expected_identity') and request['expected_identity']!=identity:raise ValueError('数据集内容身份与预期不一致')
    chosen=selection(rows,request)
    if before!={str(p):state(p) for p in checked_files(root)}:raise ValueError('数据集文件在核验期间变化')
    return {'path':str(base),'recipe':request['recipe'],'format':request['format'],'files':rows,'rows':sum(x['rows'] for x in rows),'identity':identity,'selection':chosen,'required_splits':RECIPES.get(request['recipe'],{}).get('splits',[request['split']])}
