"""显式依赖和独立评测的编排，不导入兄弟Skill实现。"""
import copy
import signal

from . import summary
from .delegation import TaskBlocked,delegate
from .evidence import EvidenceError,begin,publish,safe,save,write,ref
from .request import output
from .sources import INPUT_ERRORS,preflight

TASK_ERRORS=(*INPUT_ERRORS,RuntimeError)


def block_dependents(request,manifest):
    for item in request['evaluations']:
        row=manifest['tasks']['evaluations'][item['id']]
        if row['status']!='not_run':continue
        unavailable=[field+'/'+item[field] for field,group in (('dataset','datasets'),('deployment','deployments'))
                     if field in item and manifest['tasks'][group][item[field]]['status'] in ('failed','blocked','cancelled')]
        if unavailable:row.update(status='blocked',reason='必需依赖不可用：'+', '.join(unavailable))


def item_error(row,error,status='failed'):
    row.update(status=status,diagnostic={'type':type(error).__name__,'message':safe(error)})


def invoke(group,key,request,attempt,manifest,timeout,action):
    row=manifest['tasks'][group][key];row['status']='running';save(attempt,manifest)
    try:
        value,reference=delegate(group,key,request,attempt,manifest['run_id'],timeout,action)
        row.update(status=value['status'],receipt=reference,conclusions=value['conclusions'])
        if group not in ('evaluations','diagnoses') and not summary.dependency_pass(group,value):
            row['status']='failed' if value['status']=='failed' or 'fail' in value['conclusions'].values() else 'blocked'
        return value,reference
    except EvidenceError:raise
    except TaskBlocked as error:item_error(row,error,'blocked');return None,None
    except TASK_ERRORS as error:item_error(row,error);return None,None
    finally:save(attempt,manifest)


def dependencies(request,values,attempt,manifest,timeout):
    block_dependents(request,manifest)
    for group,field in (('datasets','dataset'),('deployments','deployment')):
        for key in request.get(group,{}):
            row=manifest['tasks'][group][key]
            if row['status']=='blocked':continue
            consumers=[x for x in request['evaluations'] if x.get(field)==key and manifest['tasks']['evaluations'][x['id']]['status']=='not_run']
            if not consumers:row.update(status='blocked',reason='没有前置输入有效的评测消费者');continue
            value,reference=invoke(group,key,values[group][key],attempt,manifest,timeout,'run')
            if group=='deployments' and row['status']=='completed':
                try:manifest['resources'].append(summary.resource(reference))
                except INPUT_ERRORS as error:item_error(row,error,'blocked')
            if value:manifest['uncovered'].extend(value['uncovered'])
            block_dependents(request,manifest)
            save(attempt,manifest)


def prepared(item,values,manifest):
    request=copy.deepcopy(values['evaluations'][item['id']])
    for field,group in (('dataset','datasets'),('deployment','deployments')):
        if field not in item:continue
        dependency=manifest['tasks'][group][item[field]]
        if dependency['status']!='completed':raise ValueError('必需依赖未完成：'+field+'/'+item[field])
        request[field]=dependency['receipt']
    return request


def prepared_diagnosis(item,manifest):
    """把定界项转成 three-step-demarcation 的请求（service 来自部署交接）。"""
    dependency=manifest['tasks']['deployments'][item['deployment']]
    if dependency['status']!='completed':raise ValueError('必需依赖未完成：deployment/'+item['deployment'])
    resource=summary.resource(dependency['receipt'])
    return {'schema_version':'1.0',
            'service':{'endpoint':resource['endpoint'],'model':resource['model']['name']},
            'badcase':item['badcase'],'render_mode':'auto'}


def evaluations(request,values,attempt,manifest,timeout,action):
    if action=='run':
        from .preflight import evaluations as check_datasets
        check_datasets(request,values,attempt,manifest,timeout,prepared)
    for item in request['evaluations']:
        key=item['id'];row=manifest['tasks']['evaluations'][key]
        if row['status']=='blocked':continue
        try:
            child=prepared(item,values,manifest);row['targets_requested']=bool(child.get('targets'))
            row['baseline_requested']=bool(child.get('baseline'))
            if action=='run' and 'deployment' in child and 'deployment' not in item:
                resource=summary.resource(child['deployment'])
                if resource not in manifest['resources']:manifest['resources'].append(resource)
        except INPUT_ERRORS as error:item_error(row,error,'blocked');save(attempt,manifest);continue
        value,reference=evaluate_item(item,child,attempt,manifest,timeout,action)
        if value:summary.collect_result(value,manifest,key,reference)
        save(attempt,manifest)


def diagnoses(request,attempt,manifest,timeout,action):
    for item in request.get('diagnoses',[]):
        key=item['id'];row=manifest['tasks']['diagnoses'][key]
        if row['status']=='blocked':continue
        try:
            child=prepared_diagnosis(item,manifest)
        except INPUT_ERRORS as error:item_error(row,error,'blocked');save(attempt,manifest);continue
        value,reference=invoke('diagnoses',key,child,attempt,manifest,timeout,action)
        if value:
            manifest['results'][key]={'receipt':reference,'conclusions':value['conclusions'],'uncovered':value['uncovered']}
            manifest['uncovered'].extend(value['uncovered'])
        save(attempt,manifest)


def auto_diagnosis_request(anomaly, attempt, diag_id):
    """把异常样本转成 three-step-demarcation 请求（badcase 写成文件引用）。"""
    folder = attempt / 'auto-badcases'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (diag_id + '.json')
    write(path, anomaly['request'])
    return {'schema_version': '1.0', 'service': anomaly['service'],
            'badcase': ref(path), 'render_mode': 'auto'}


def auto_diagnoses(request, attempt, manifest, timeout, action):
    """评测产出异常后，自动构造定界请求并委派（方案 B 自动衔接）。"""
    if request.get('auto_diagnose') is False:
        return  # 显式关闭自动诊断
    for eval_id, result in manifest['results'].items():
        if eval_id not in manifest['tasks'].get('evaluations', {}):
            continue  # 只处理评测结果，跳过 diagnoses 等其它组
        for position, anomaly in enumerate(result.get('anomalies', [])):
            diag_id = 'auto-%s-%s-%s' % (eval_id, anomaly.get('sample_id', position), anomaly.get('anomaly_type', position))
            row = manifest['tasks']['diagnoses'].setdefault(diag_id, {'status': 'not_run'})
            if row['status'] == 'blocked':
                continue
            try:
                child = auto_diagnosis_request(anomaly, attempt, diag_id)
            except INPUT_ERRORS as error:
                item_error(row, error, 'blocked'); save(attempt, manifest); continue
            value, reference = invoke('diagnoses', diag_id, child, attempt, manifest, timeout, action)
            if value:
                manifest['results'][diag_id] = {'receipt': reference, 'conclusions': value['conclusions'], 'uncovered': value['uncovered']}
                manifest['uncovered'].extend(value['uncovered'])
            save(attempt, manifest)


def evaluate_item(item, child, attempt, manifest, timeout, action):
    if 'result' not in item:
        return invoke('evaluations', item['id'], child, attempt, manifest, timeout, action)
    from .reuse import evaluation
    row = manifest['tasks']['evaluations'][item['id']]
    try:
        value, reference, check = evaluation(item, child, attempt, manifest['run_id'], timeout)
        row.update(status='completed', receipt=reference, conclusions=value['conclusions'],
                   mode='reuse', revalidation=check)
        return value, reference
    except EvidenceError:
        raise
    except TASK_ERRORS as error:
        item_error(row, error, 'blocked')
        return None, None


def cancel_remaining(manifest):
    for group in manifest['tasks'].values():
        for row in group.values():
            if row['status']=='running':row.update(status='failed',reason='本次阶段取消')
            elif row['status']=='not_run':row.update(status='cancelled',reason='阶段取消，未启动该项')


def run(request,work_root,run_id,action):
    values,errors,protected=preflight(request)
    parent=output(work_root,run_id,protected)
    attempt,manifest=begin(request,parent,run_id,action)
    for (group,key),reason in errors.items():manifest['tasks'][group][key].update(status='blocked',reason=reason)
    try:
        timeout=request.get('timeout_seconds',14400)
        dependencies(request,values,attempt,manifest,timeout)
        evaluations(request,values,attempt,manifest,timeout,action)
        diagnoses(request,attempt,manifest,timeout,action)
        auto_diagnoses(request,attempt,manifest,timeout,action)
        summary.conclude(manifest)
    except EvidenceError:raise
    except KeyboardInterrupt as error:
        cancel_remaining(manifest);summary.conclude(manifest)
        manifest.update(status='failed',success=False,next_action='cancelled')
        manifest['conclusions']['precision_testing']='fail';manifest['errors'].append(safe(error))
    return finish(attempt,manifest)


def finish(attempt,manifest):
    try:return publish(attempt,manifest)
    except KeyboardInterrupt:
        previous={sig:signal.signal(sig,signal.SIG_IGN) for sig in (signal.SIGINT,signal.SIGTERM)}
        try:
            cancel_remaining(manifest);manifest.update(status='failed',success=False,next_action='cancelled')
            manifest['conclusions']['precision_testing']='fail'
            return publish(attempt,manifest)
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
