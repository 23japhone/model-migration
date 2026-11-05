"""公开回执外层、执行来源及唯一输入身份。"""
from pathlib import Path
import json

from .evidence import bound,read,ref

TASKS = {'precheck':'vllm-model-precheck','register':'vllm-model-register','verify':'verify-agent','adapt':'adapt-agent'}
VERIFY_KEYS = {'inputs','runtime','registration','workers','functional_protocol','semantic','lifecycle'}


def common(value,task):
    if value.get('schema_version') != '1.0' or value.get('task_id') != task:raise ValueError('回执任务或schema身份不符')
    if not isinstance(value.get('run_id'),str) or not value['run_id']:raise ValueError('回执缺少run_id')
    for key,kind in [('inputs',dict),('conclusions',dict),('evidence',dict),('uncovered',list)]:
        if not isinstance(value.get(key),kind):raise ValueError('回执共同字段无效：'+key)
    if any(not isinstance(v,str) for v in value['evidence'].values()) or any(not isinstance(v,str) for v in value['uncovered']):
        raise ValueError('共同证据和未覆盖项必须为文本')
    if any(v not in ('pass','fail','not_evaluated','review_required') for v in value['conclusions'].values()):
        raise ValueError('回执结论值无效')
    return value


def completed(reference,task,conclusion):
    value = common(bound(reference),task)
    if value.get('status') != 'completed' or value['conclusions'].get(conclusion) != 'pass':
        raise ValueError('交接未完成必需检查：'+task)
    return value


def child(text,code,name,expected,root,run_id):
    result = reply(text)
    path = Path(result.get('manifest',result.get('report_path','')))
    if not path.is_absolute() or not path.is_relative_to(root):raise ValueError('子任务回执不属于本次运行')
    value = common(read(path),TASKS[name])
    if value['run_id'] != run_id or value['inputs'] != expected:raise ValueError('子任务输入或run_id不匹配')
    status = value['status']
    valid = {'completed':0,'blocked':3,'failed':1}
    wanted = 1 if name=='verify' and status=='completed' and not value.get('success') else valid.get(status)
    if wanted is None or code != wanted:raise ValueError('子任务退出码与回执不一致')
    return value,ref(path)


def reply(text):
    try:value=json.loads(text)
    except (ValueError,RecursionError) as error:raise ValueError('子任务回执不是有效JSON对象') from error
    if not isinstance(value,dict):raise ValueError('子任务回执必须为JSON对象')
    return value


def plan(text,code):
    value=reply(text)
    if code or value.get('status')!='not_run' or not isinstance(value.get('request'),dict):
        raise ValueError('子任务计划无效')
    return value['request']


def verification(value):
    if set(value['conclusions']) != VERIFY_KEYS:raise ValueError('验证必需结论不完整')
    return value.get('status')=='completed' and value.get('success') is True and all(v=='pass' for v in value['conclusions'].values())


def outcome(value):
    if verification(value):return 'completed'
    if value['status']=='failed' or 'fail' in value['conclusions'].values():return 'failed'
    other = [v for k,v in value['conclusions'].items() if k!='semantic']
    if value['status']=='completed' and value['conclusions']['semantic']=='review_required' and all(v=='pass' for v in other):
        return 'awaiting_review'
    return 'blocked'
