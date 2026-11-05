"""核对数据集来源、格式和本次样本范围。"""
import json
from pathlib import Path,PurePosixPath
import re
from urllib.parse import urlsplit

FIELDS={'source','recipe','path','url','split','subjects','format','required_fields','csv_header','selection','expected_sha256','expected_identity','revision','max_bytes','timeout_seconds','files','supporting_files','layout','default_local'}
RECIPES=json.loads((Path(__file__).resolve().parents[2]/'references/recipes.json').read_text())


def digest(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('SHA256 必须为64位小写摘要')
    return value


def relative(value):
    if not isinstance(value,str) or not value or '\\' in value or '\0' in value:raise ValueError('文件名必须为非空相对路径')
    p=PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or p.as_posix()!=value or value=='.':raise ValueError('文件名必须为规范化相对路径')
    return value


def normalize_selection(value):
    value=selection_fields(value)
    result={'offset':value.get('offset',0)}
    if 'file' in value:result['file']=relative(value['file'])
    if type(result['offset']) is not int or result['offset']<0:raise ValueError('offset 必须为非负整数')
    for name in ('limit','seed'):
        if name in value:
            if type(value[name]) is not int:raise ValueError('limit 和 seed 必须为整数')
            result[name]=value[name]
    if 'limit' in result and result['limit']<1:raise ValueError('limit 必须为正整数')
    if 'seed' in result and 'limit' not in result:raise ValueError('随机样本选择必须显式提供 limit')
    return result


def selection_fields(value):
    if not isinstance(value,dict) or set(value)-{'offset','limit','seed','file'}:
        raise ValueError('selection 只接受 offset、limit、seed、file')
    return {key:item for key,item in value.items() if key!='limit' or item is not None}


def normalize_request(value,base=None):
    if not isinstance(value,dict) or set(value)-FIELDS:raise ValueError('请求只能包含契约字段，不能包含凭据')
    source=value.get('source');recipe=value.get('recipe','generic')
    if source not in ('local','http','recipe') or recipe not in (*RECIPES,'generic'):raise ValueError('source 或 recipe 无效')
    if source=='recipe' and recipe=='generic':raise ValueError('通用来源必须明确 local 或 http')
    path=value.get('path')
    if not isinstance(path,str) or not path.strip():raise ValueError('必须提供资产 path')
    split=value.get('split')
    if not isinstance(split,str) or not split.strip():raise ValueError('必须明确本次 split')
    if recipe in RECIPES and split not in RECIPES[recipe]['splits']:raise ValueError('split 不属于所选配方支持范围')
    result={**value,'source':source,'recipe':recipe,'split':split,'path':str((Path(base or Path.cwd())/Path(path).expanduser()).resolve()),'selection':normalize_selection(value.get('selection',{}))}
    normalize_source(result)
    normalize_format(result)
    normalize_layout(result)
    normalize_default_local(result)
    return result


def absolute(value):
    if not isinstance(value,str) or not value or '\0' in value:
        raise ValueError('默认目录及资料证据必须为规范绝对路径')
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts or str(path)!=value:
        raise ValueError('默认目录及资料证据必须为规范绝对路径')
    return value


def normalize_default_local(value):
    if 'default_local' not in value:return
    if value['source']=='local':raise ValueError('default_local 仅用于 http/recipe 下载来源的缺路径复用')
    candidate=value['default_local']
    if not isinstance(candidate,dict) or set(candidate)!={'path','evidence'}:
        raise ValueError('default_local 必须包含 path 和官方资料 evidence 引用')
    evidence=candidate['evidence']
    if not isinstance(evidence,dict) or set(evidence)!={'path','sha256'}:
        raise ValueError('default_local.evidence 必须为 path/sha256 引用')
    value['default_local']={'path':absolute(candidate['path']),
                            'evidence':{'path':absolute(evidence['path']),'sha256':digest(evidence['sha256'])}}


def normalize_layout(value):
    layout=value.get('layout','raw')
    if layout not in ('raw','evalscope'):
        raise ValueError('layout 仅支持 raw 或 evalscope')
    if layout=='evalscope':
        if value['recipe'] not in ('ceval','gsm8k'):
            raise ValueError('evalscope 布局仅支持 ceval/gsm8k 配方')
        if value.get('supporting_files'):
            raise ValueError('evalscope 布局自动确定元数据，不能另外指定 supporting_files')


def normalize_source(value):
    if value['source']=='local':
        if {'url','revision','expected_sha256'} & value.keys():raise ValueError('本地来源不能声明已核对的上游下载身份；可使用 expected_identity')
    else:
        value['url']=source_url(value)
        if 'expected_sha256' in value:digest(value['expected_sha256'])
    normalize_limits(value)
    normalize_identity(value)


def source_url(value):
    url=value.get('url') or RECIPES.get(value['recipe'],{}).get('url')
    p=urlsplit(url) if isinstance(url,str) else None
    if p is None or p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError('来源必须是不含凭据和查询参数的 HTTPS 链接')
    return url


def normalize_limits(value):
    for name,default,minimum,maximum in [('max_bytes',512*1024**2,1,16*1024**3),('timeout_seconds',30,1,3600)]:
        value[name]=value.get(name,default)
        if type(value[name]) is not int or not minimum<=value[name]<=maximum:raise ValueError(f'{name} 超出允许范围')


def normalize_identity(value):
    if 'revision' in value and (not isinstance(value['revision'],str) or not value['revision'].strip()):raise ValueError('revision 说明不能为空')
    if 'expected_identity' in value:
        identity=value['expected_identity']
        if not isinstance(identity,dict) or set(identity)!={'format','sha256'} or identity['format']!='vllm-dataset-file-list-v1':raise ValueError('expected_identity 格式无效')
        digest(identity['sha256'])


def normalize_format(value):
    value['format']=value.get('format',RECIPES.get(value['recipe'],{}).get('format'))
    if value['format'] not in ('jsonl','json','csv'):raise ValueError('格式只支持 JSONL、JSON 对象列表与 CSV')
    if value['recipe'] in RECIPES and value['format']!=RECIPES[value['recipe']]['format']:raise ValueError('不能改变所选配方的原始格式')
    normalize_lists(value)
    for filename in value['files']:relative(filename)
    if value['recipe']!='generic' and value['files']:raise ValueError('配方文件由必需 split 布局确定，不能用 files 绕过')
    if value['recipe']=='gsm8k' and value['subjects']:raise ValueError('GSM8K 不使用 subjects')
    if type(value.get('csv_header',True)) is not bool:raise ValueError('csv_header 必须为布尔值')
    value['csv_header']=value.get('csv_header',True)


def normalize_lists(value):
    for name in ('subjects','required_fields','files'):
        items=value.get(name,[])
        if not isinstance(items,list) or any(not isinstance(x,str) or not x.strip() for x in items):raise ValueError(f'{name} 必须为非空字符串列表')
        if len(set(items))!=len(items):raise ValueError(f'{name} 不能包含重复项')
        value[name]=list(items) if name=='required_fields' else sorted(items)
    if 'supporting_files' in value:
        supporting=value['supporting_files']
        if not isinstance(supporting,list) or any(not isinstance(x,str) for x in supporting):
            raise ValueError('supporting_files 必须为相对路径列表')
        for filename in supporting:relative(filename)
        if len(set(supporting))!=len(supporting) or set(supporting)&set(value['files']):
            raise ValueError('支持文件不能重复或兼作样本文件')
