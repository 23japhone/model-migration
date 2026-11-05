"""逐项核验引用图，完整收集来源和资产边界后交给编排。"""
from pathlib import Path

from .evidence import bound,safe
from .request import reference as validate_reference,roots

INPUT_ERRORS=(OSError,ValueError,KeyError,TypeError,RecursionError)
NATIVE_FORMATS={'evalscope-native-v1','aisbench-native-v1'}


def report_roots(references):
    return [Path(ref['path']).parent for ref in references if isinstance(ref,dict)
            and isinstance(ref.get('path'),str) and Path(ref['path']).is_absolute()]


def scan(references,seen,protected,depth):
    errors=[]
    for reference in references:
        try:follow(reference,seen,protected,depth)
        except INPUT_ERRORS as error:errors.append(safe(error))
    return errors


def linked(value,seen,protected,depth=0):
    if depth>8:raise ValueError('交接来源链超过8层')
    protected.extend(roots(value))
    required=[value[key] for key in ('source','deployment','dataset') if key in value]
    format_name=value.get('format')
    native='config_index' in value or (isinstance(format_name,str) and format_name in NATIVE_FORMATS)
    if native:
        required.append(value.get('config_index'))
    baseline=value.get('baseline') if isinstance(value.get('baseline'),dict) else {}
    protected.extend(report_roots([*required,baseline.get('report')]))
    errors=scan(required,seen,protected,depth)
    if native:
        try:validate_reference(value.get('config_index'))
        except INPUT_ERRORS as error:errors.append(safe(error))
    if value.get('task_id') and isinstance(value.get('inputs'),dict):
        try:linked(value['inputs'],seen,protected,depth+1)
        except INPUT_ERRORS as error:errors.append(safe(error))
    scan(baseline.values(),seen,protected,depth)  # 可选基线错误由精度任务报告；边界仍收集。
    if errors:raise ValueError('；'.join(errors))


def follow(reference,seen,protected,depth):
    if not isinstance(reference,dict) or set(reference)!={'path','sha256'}:return
    marker=(reference['path'],reference['sha256'])
    if marker in seen:raise ValueError('交接来源循环')
    child=bound(reference)
    if child.get('task_id') or 'format' in child:
        protected.append(Path(reference['path']).parent)
    linked(child,seen|{marker},protected,depth+1)


def retention(value):
    service=value.get('service')
    if not isinstance(service,dict) or service.get('retention')!='keep':
        raise ValueError('部署请求必须显式retention=keep，阶段不改写服务策略')


def preflight(request):
    values={group:{} for group in ('datasets','deployments','evaluations')};errors={};protected=roots(request)
    for group,refs in [(g,request.get(g,{})) for g in ('datasets','deployments')]+[('evaluations',{x['id']:x['request'] for x in request['evaluations'] if 'request' in x})]:
        for key,reference in refs.items():
            try:
                value=bound(reference);values[group][key]=value;linked(value,set(),protected)
                if group=='deployments':retention(value)
            except INPUT_ERRORS as error:errors[(group,key)]=safe(error)
    for item in request['evaluations']:
        continuation_source(item, protected, errors)
        value=values['evaluations'].get(item['id'],{})
        for field in ('dataset','deployment'):
            if field in item and field in value:errors[('evaluations',item['id'])]='不得用依赖覆盖已有'+field+'交接'
    return values,errors,protected


def continuation_source(item, protected, errors):
    if 'blocked_reason' in item:
        errors[('evaluations', item['id'])] = item['blocked_reason']
    if 'result' in item:
        try:
            follow(item['result'], set(), protected, 0)
        except INPUT_ERRORS as error:
            errors[('evaluations', item['id'])] = safe(error)
