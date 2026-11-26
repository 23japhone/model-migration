"""阶段纯形状、引用和输出边界；计划不读取引用。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import re



def fields(value,required,optional=()):
    if not isinstance(value,dict) or set(required)-value.keys() or value.keys()-set(required)-set(optional):
        raise ValueError('对象字段缺失或含未知字段')


def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}',value):
        raise ValueError('标识必须为安全单层名称')


def absolute(value):
    if not isinstance(value,str) or '\0' in value or not Path(value).is_absolute() or str(Path(value))!=value or '..' in Path(value).parts:
        raise ValueError('需要规范绝对路径')


def reference(value):
    fields(value,('path','sha256'));absolute(value['path'])
    if not isinstance(value['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',value['sha256']):raise ValueError('SHA256无效')


def dependencies(value):
    for group in ('datasets','deployments'):
        table=value.get(group,{})
        if not isinstance(table,dict):raise ValueError('依赖必须为名称到请求引用的对象')
        for key,item in table.items():name(key);reference(item)
        field='dataset' if group=='datasets' else 'deployment'
        used={row[field] for row in value['evaluations'] if field in row}
        if group=='deployments':
            used |= {row['deployment'] for row in value.get('diagnoses',[])}
        if used!=set(table):raise ValueError('依赖缺失或没有实际评测消费者：'+group)


def validate(value,action):
    fields(value,('schema_version','evaluations'),('datasets','deployments','diagnoses','auto_diagnose','timeout_seconds'))
    if value['schema_version']!='1.0':raise ValueError('仅支持schema_version=1.0')
    rows=value['evaluations']
    if not isinstance(rows,list) or not rows:raise ValueError('至少提供一个评测项')
    for row in rows:
        evaluation_row(row, action)
        for key in ('dataset','deployment'):
            if key in row:name(row[key])
    if len({row['id'] for row in rows})!=len(rows):raise ValueError('评测项标识重复')
    diagnoses=value.get('diagnoses',[])
    if not isinstance(diagnoses,list):raise ValueError('diagnoses 须为数组')
    for row in diagnoses:diagnosis_row(row)
    if len({row['id'] for row in diagnoses})!=len(diagnoses):raise ValueError('定界项标识重复')
    dependencies(value)
    if action=='analyze' and (value.get('datasets') or value.get('deployments')):
        raise ValueError('历史分析不得调用准备或部署任务')
    if 'auto_diagnose' in value and type(value['auto_diagnose']) is not bool:
        raise ValueError('auto_diagnose 须为布尔值')
    timeout=value.get('timeout_seconds',14400)
    if type(timeout)!=int or not 1<=timeout<=86400:raise ValueError('超时须为1—86400秒')
    return value


def evaluation_row(row, action):
    if 'blocked_reason' in row:
        fields(row, ('id', 'blocked_reason'))
        name(row['id'])
        if not isinstance(row['blocked_reason'], str) or not row['blocked_reason'].strip():
            raise ValueError('未就绪评测须有明确前置缺口')
        return
    fields(row, ('id', 'request'), ('dataset', 'deployment', 'result'))
    name(row['id']); reference(row['request'])
    if 'result' in row:
        if action != 'run':
            raise ValueError('精度结果复用仅用于 run 续接')
        reference(row['result'])


def diagnosis_row(row):
    fields(row, ('id', 'badcase', 'deployment'))
    name(row['id']); reference(row['badcase']); name(row['deployment'])


def path_values(value):
    candidates=value if isinstance(value,list) else [value]
    return [Path(item) for item in candidates if isinstance(item,str) and item.startswith('/')]


def roots(value):
    if isinstance(value,dict):
        paths=('path','root','runtime_root','resources','context','output_root','protected_roots','weight_files')
        return [p for key,item in value.items() for p in
                ([*path_values(item),*roots(item)] if key in paths else roots(item))]
    if isinstance(value,list):
        return [p for item in value for p in roots(item)]
    return []


def output(work_root,run_id,protected=()):
    name(run_id)
    target=Path(work_root).expanduser().absolute()/run_id/'vllm-precision-align'
    if target.resolve()!=target:raise ValueError('输出不能经符号链接重定向')
    installed=release_root()
    for boundary in (installed,*protected):
        boundary=Path(boundary).resolve()
        if target.is_relative_to(boundary) or boundary.is_relative_to(target):raise ValueError('运行目录须位于完整资源、来源和资产之外')
    return target
