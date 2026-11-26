"""汇总公共测量结论及部署资源，不重新评分或启停服务。"""
from .evidence import bound
from .delegation import VERIFY_KEYS


def dependency_pass(group,value):
    if value['status']!='completed':return False
    if group=='datasets':return value['conclusions'].get('assets')=='pass'
    return value.get('success') is True and all(value['conclusions'].get(key)=='pass' for key in VERIFY_KEYS)


def resource(reference):
    report=bound(reference);raw=report
    if report.get('operation')=='review':raw=bound(report['inputs']['source'])
    handoff=bound({'path':raw['evidence']['handoff'],'sha256':raw['detailed_report']['handoff_sha256']})
    final=handoff.get('final_service')
    if not isinstance(final,dict) or final.get('state')!='retained':raise ValueError('精度服务未保留或资源交接无效')
    return {'deployment':reference,'handoff':{'path':raw['evidence']['handoff'],'sha256':raw['detailed_report']['handoff_sha256']},
            'lifecycle_owner':'verify-agent','stage_ownership':'delegated',
            'service_ownership':raw['inputs']['service']['ownership'],'endpoint':handoff['endpoint'],
            'model':handoff.get('model'),'workers':handoff.get('workers'),'final_service':handoff['final_service'],
            'state_observation':'部署任务交接；当前服务核验见精度任务运行证据'}


def collect_result(value,manifest,key,receipt):
    manifest['results'][key]={'receipt':receipt,'conclusions':value['conclusions'],
        'measurements':value.get('detailed_report',{}).get('measurements',[]),
        'conditions':value.get('detailed_report',{}).get('conditions'),
        'comparison':value.get('detailed_report',{}).get('comparison'), 'uncovered':value['uncovered'],
        'anomalies':value.get('anomalies',[])}
    manifest['uncovered'].extend(value['uncovered'])


def verdict(values):
    if 'fail' in values:return 'fail'
    if 'review_required' in values:return 'review_required'
    return 'pass' if values and all(x=='pass' for x in values) else 'not_evaluated'


def execution_status(manifest):
    items=[item for group in ('datasets','deployments','evaluations')
           for item in manifest['tasks'].get(group,{}).values()]
    rows=list(manifest['tasks']['evaluations'].values())
    if all(x['status']=='completed' for x in rows):return 'completed'
    return 'failed' if any(x['status']=='failed' for x in items) else 'blocked'


def applicable(manifest,key,requested):
    rows=manifest['tasks']['evaluations']
    values=[row['conclusions'][key] for row in rows.values() if row.get('conclusions')
            and (row.get(requested) or row['conclusions'][key]!='not_evaluated')]
    result=verdict(values)
    if result=='pass' and manifest.get('status')!='completed':return 'not_evaluated'
    return result


def conclude(manifest):
    status=execution_status(manifest)
    manifest['status']=status
    rows=manifest['tasks']['evaluations'].values()
    validity=[row['conclusions']['evaluation_validity'] for row in rows if row.get('conclusions')]
    if status!='completed':validity.append('fail' if status=='failed' else 'review_required')
    manifest['conclusions']['evaluation_validity']=verdict(validity)
    manifest['conclusions']['precision_testing']='pass' if status=='completed' else ('fail' if status=='failed' else 'review_required')
    manifest['conclusions']['comparability']=applicable(manifest,'comparability','baseline_requested')
    manifest['conclusions']['precision_target']=applicable(manifest,'precision_target','targets_requested')
    manifest['conclusions']['demarcation']=verdict([
        row.get('conclusions',{}).get('demarcation','not_evaluated')
        for row in manifest['tasks'].get('diagnoses',{}).values()])
    targets=[row for row in rows if row.get('targets_requested')]
    targets_pass=all(row.get('conclusions',{}).get('precision_target')=='pass' for row in targets)
    manifest['success']=status=='completed' and manifest['conclusions']['evaluation_validity']=='pass' and targets_pass
    manifest['next_action']='complete' if status=='completed' else 'resolve_failed_tasks' if status=='failed' else 'provide_missing_inputs_or_review'
