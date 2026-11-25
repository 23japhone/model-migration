"""固定公开CLI的计划、输入和执行回执绑定。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import sys

from .evidence import parse,read,ref,write
from .process import call

ENTRIES={'datasets':('vllm-dataset-prepare','prepare.py'),
         'deployments':('verify-agent','verify.py'),'evaluations':('verify-agent-plus','validator.py')}
VERIFY_KEYS={'inputs','runtime','registration','workers','functional_protocol','semantic','lifecycle'}
VERDICTS={'pass','fail','not_evaluated','review_required'}


class TaskBlocked(ValueError):
    """任务未安装或公开计划明确拒绝输入，尚未执行效果。"""


def common(value,task):
    if value.get('schema_version')!='1.0' or value.get('task_id')!=task:raise ValueError('子任务身份不一致')
    for key,kind in (('inputs',dict),('conclusions',dict),('evidence',dict),('uncovered',list)):
        if not isinstance(value.get(key),kind):raise ValueError('子任务共同字段无效：'+key)
    if any(x not in VERDICTS for x in value['conclusions'].values()):raise ValueError('子任务结论无效')
    if any(not isinstance(x,str) for x in value['uncovered']):raise ValueError('未覆盖项必须为文本')


def exit_code(value,group):
    status=value.get('status')
    if status=='blocked':return 3
    if status=='failed':return 1
    if status!='completed':raise ValueError('子任务未给出执行终态')
    conclusions=value['conclusions']
    if group=='evaluations':
        if set(conclusions)!={'evaluation_validity','comparability','precision_target'} or conclusions['evaluation_validity']!='pass':
            raise ValueError('completed精度回执缺少有效测量')
        expected=not value['inputs'].get('targets') or conclusions['precision_target']=='pass'
        if value.get('success') is not expected:raise ValueError('精度success与目标不一致')
        return 0 if expected else 1
    if group=='deployments':
        if set(conclusions)!=VERIFY_KEYS or type(value.get('success'))!=bool:raise ValueError('部署必需结论不完整')
        return 0 if value['success'] else 1
    if conclusions.get('assets')!='pass':raise ValueError('数据完成回执缺少assets=pass')
    return 0


def identity(value,group,request,run_id,action):
    common(value,ENTRIES[group][0])
    if group!='datasets' and value.get('operation')!=('verify' if group=='deployments' else action):
        raise ValueError('子任务操作身份与本次委托不一致')
    if value.get('run_id')!=run_id or value['inputs']!=request:raise ValueError('子任务run_id或输入不一致')


def precision_details(value):
    details=value.get('detailed_report',{})
    if not isinstance(details,dict):raise ValueError('精度回执详情必须为对象')
    rows=details.get('measurements',[])
    if not isinstance(rows,list):raise ValueError('精度测量必须为数组')
    for row in rows:
        if not isinstance(row,dict) or type(row.get('valid'))!=bool or not isinstance(row.get('metrics'),dict):
            raise ValueError('精度测量结构无效')
    if value.get('status')=='completed' and (not rows or any(not row['valid'] or not row['metrics'] for row in rows)):
        raise ValueError('completed精度回执缺少完整有效测量')
    conditions=details.get('conditions')
    if conditions is not None and not isinstance(conditions,dict):raise ValueError('实际评测条件必须为对象或null')


def receipt(text,code,group,request,root,run_id,action):
    printed=parse(text);location=printed.get('manifest',printed.get('report_path'))
    if location is None:
        if group!='evaluations' or printed.get('status')!='blocked' or code!=3:raise ValueError('子任务缺少持久回执')
        root.mkdir(parents=True,exist_ok=True);path=root/'blocked-inline.json';write(path,printed);value=printed
    else:
        path=Path(location)
        if not path.is_absolute() or path.resolve()!=path or not path.is_relative_to(root):raise ValueError('子任务回执越出本次运行')
        value=read(path)
        for key in ('status','conclusions','inputs','run_id','task_id','operation','success'):
            if key in printed and printed[key]!=value.get(key):raise ValueError('子任务stdout与保存回执不一致')
    identity(value,group,request,run_id,action)
    if group=='evaluations' and action=='preflight':
        from .preflight import receipt as preflight_receipt
        preflight_receipt(value,code)
        return value,ref(path)
    if group=='evaluations':precision_details(value)
    if code!=exit_code(value,group):raise ValueError('子任务退出码与回执不一致')
    return value,ref(path)


def delegate(group,key,request,attempt,run_id,timeout,action):
    skill,entry=ENTRIES[group];cli=release_skill(skill)/'scripts'/entry
    if not cli.is_file():raise TaskBlocked('缺少公开任务资源：'+skill+'/'+entry)
    root=attempt/'children'/group/key;label=group+'-'+key
    if action=='preflight':
        root=root/'preflight';label+='-preflight'
    path=attempt/(label+'.request.json');write(path,request)
    args=[sys.executable,'-B',str(cli),'--request',str(path),'--work-root',str(root),'--run-id',run_id]
    if group=='evaluations':args.extend(('--action',action))
    code,text=call([*args,'--plan'],min(timeout,60),attempt,label+'-plan')
    if code in (2,3):raise TaskBlocked('子任务计划拒绝输入或缺少前置条件，查看plan的stderr')
    plan=parse(text);normalized=plan.get('inputs' if group=='evaluations' else 'request')
    if code or plan.get('status')!='not_run' or not isinstance(normalized,dict):raise ValueError('子任务计划无效')
    if group!='datasets' and normalized!=request:raise ValueError('子任务计划意外改写请求')
    write(path,normalized)
    code,text=call(args,timeout,attempt,label)
    return receipt(text,code,group,normalized,root,run_id,action)
