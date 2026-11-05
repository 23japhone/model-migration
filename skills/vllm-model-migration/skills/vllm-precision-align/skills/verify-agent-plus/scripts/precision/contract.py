"""精度任务的请求形状和输出隔离边界；计划不解引用。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
import math
from pathlib import Path
import re
from .backends import resolve


def object_fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value)-set(required)-set(optional) or set(required)-set(value):
        raise ValueError('请求对象缺少必需字段或含未知字段')


def absolute(value):
    if not isinstance(value,str) or not Path(value).is_absolute() or str(Path(value))!=value:
        raise ValueError('路径必须为规范绝对路径')
    return value


def reference(value):
    object_fields(value, ('path','sha256'))
    absolute(value['path'])
    if not isinstance(value['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',value['sha256']):
        raise ValueError('文件引用 SHA256 无效')


def name(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value):
        raise ValueError('任务标识须为单层安全名称')


def metric(spec):
    object_fields(spec, ('unit','direction'), ('path','selector'))
    if spec['unit'] not in ('percent','ratio','value') or spec['direction'] not in ('higher','lower'):
        raise ValueError('指标单位或方向无效')
    if 'path' in spec and (not isinstance(spec['path'],list) or not spec['path']
                           or not all(isinstance(x,str) and x for x in spec['path'])):
        raise ValueError('指标路径须为非空字符串数组')
    if 'selector' in spec:
        selector(spec['selector'])


def selector(value):
    object_fields(value, ('name','aggregation','dimensions'))
    name(value['name']);name(value['aggregation'])
    if not isinstance(value['dimensions'],dict):raise ValueError('指标 dimensions 必须为对象')
    for key,item in value['dimensions'].items():
        name(key)
        if type(item) not in (str,int,float,bool):raise ValueError('指标 dimensions 值必须为标量')
        if type(item)==float and not math.isfinite(item):raise ValueError('指标 dimensions 值必须有限')


def task(value, backend='aisbench'):
    optional = ('source_columns',) if backend=='aisbench' else ('benchmark','subset')
    object_fields(value, ('dataset','model_abbr','sample_ids','metrics'), optional)
    name(value['dataset']);name(value['model_abbr'])
    ids = value['sample_ids']
    if not isinstance(ids,list) or not ids or any(type(x)!=int or x<0 for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('样本 id 须为唯一非负整数数组')
    task_metrics(value['metrics'],backend)
    if 'source_columns' in value:source_columns(value['source_columns'])
    if backend=='evalscope':
        name(value.get('benchmark'));name(value.get('subset'))


def task_metrics(metrics,backend):
    if not isinstance(metrics,dict) or not metrics:
        raise ValueError('必须声明实际指标')
    for key,spec in metrics.items():
        name(key);metric(spec)
        if backend=='evalscope' and ('selector' not in spec or 'path' in spec):
            raise ValueError('EvalScope 指标须使用明确的 selector')
        if backend=='aisbench' and 'selector' in spec:raise ValueError('AISBench 指标继续使用 path')


def source_columns(value):
    if not isinstance(value,dict) or not value:raise ValueError('source_columns 须为非空字段映射')
    for key,path in value.items():
        if not isinstance(key,str) or not key:raise ValueError('reader 列名无效')
        parts=path if isinstance(path,list) else [path]
        if not parts or any(type(x) not in (str,int) for x in parts):raise ValueError('原始列路径无效')


def target(value):
    object_fields(value, ('dataset','metric','kind','value','unit'))
    name(value['dataset']);name(value['metric'])
    if value['kind'] not in ('absolute','max_drop','max_relative_drop'):
        raise ValueError('目标类型无效')
    if type(value['value']) not in (int,float) or not math.isfinite(value['value']):
        raise ValueError('目标阈值必须为有限数字')
    if value['unit'] not in ('percent','ratio','value'):
        raise ValueError('目标单位无效')
    if value['kind']!='absolute' and value['value']<0:
        raise ValueError('允许下降量必须非负')
    if value['kind']=='max_relative_drop' and value['unit']!='percent':
        raise ValueError('相对下降率单位必须为 percent')


def measurement(request):
    object_fields(request, ('schema_version','deployment','dataset','evaluator','tasks'), ('baseline','targets'))
    if request['schema_version']!='1.0':raise ValueError('不支持的请求版本')
    reference(request['deployment']);reference(request['dataset'])
    ev = request['evaluator']
    object_fields(ev, ('python','config'), ('timeout_seconds','backend','version'))
    absolute(ev['python']);reference(ev['config'])
    backend,_=resolve(ev)
    timeout = ev.get('timeout_seconds',14400)
    if type(timeout)!=int or not 1<=timeout<=86400:raise ValueError('评测超时须为1至86400秒')
    if not isinstance(request['tasks'],list) or not request['tasks']:raise ValueError('缺少评测任务')
    for item in request['tasks']:task(item,backend)
    if len({x['dataset'] for x in request['tasks']})!=len(request['tasks']):raise ValueError('数据任务标识重复')


def validate(request, action):
    if action=='analyze':
        object_fields(request, ('source',), ('baseline','targets'))
        reference(request['source'])
        if 'request' in request.get('baseline',{}):raise ValueError('分析只接受已有基线报告，不能发起基线测量')
    else:measurement(request)
    targets = request.get('targets',[])
    if not isinstance(targets,list):raise ValueError('targets 须为数组')
    for value in targets:target(value)
    if 'baseline' in request:
        value=request['baseline'];object_fields(value, (), ('report','request'))
        if len(value)!=1:raise ValueError('基线须指定唯一 report 或 request')
        reference(next(iter(value.values())))
    if action!='analyze':target_links(request)
    return request


def target_links(request):
    available={(item['dataset'],key):spec for item in request['tasks'] for key,spec in item['metrics'].items()}
    for value in request.get('targets',[]):
        spec=available.get((value['dataset'],value['metric']))
        if spec is None:raise ValueError('目标引用了未声明的数据任务或指标')
        if value['kind']!='max_relative_drop' and value['unit']!=spec['unit']:
            raise ValueError('目标单位与指标不一致')


def output_parent(work_root, run_id, protected=()):
    name(run_id)
    root=Path(work_root).expanduser().absolute()
    target_path=root/run_id/'verify-agent-plus'
    check_output(target_path,protected)
    return target_path


def check_output(target_path,protected=()):
    target_path=Path(target_path)
    root=target_path
    if root.resolve()!=root or target_path.resolve()!=target_path:
        raise ValueError('输出不得通过符号链接重定向')
    boundary = release_root()
    for item in (boundary,*protected):
        item=Path(item).resolve()
        if target_path.is_relative_to(item) or item.is_relative_to(target_path):
            raise ValueError('输出必须位于完整技能包、来源和资产之外')
