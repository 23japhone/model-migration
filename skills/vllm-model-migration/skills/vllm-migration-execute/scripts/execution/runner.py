"""依赖路线、不可变阶段尝试与部署交接聚合。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from datetime import datetime,timezone
from pathlib import Path
import sys
import signal
import uuid

from . import binding,receipts,transport
from .evidence import EvidenceError,bound,identity,ref,write
from .process import call
from .request import output,roots

ERRORS = (OSError,ValueError,KeyError,TypeError,RuntimeError,RecursionError)
ENTRIES = {'precheck':('vllm-model-precheck','precheck.py'),
           'register':('vllm-model-register','register.py'),'verify':('verify-agent','verify.py')}


def now():return datetime.now(timezone.utc).isoformat()


def preflight(request):
    values,errors = {},[]
    for name in ['preparation','precheck','verify','register']:
        if name not in request:continue
        try:values[name] = bound(request[name])
        except ERRORS as error:errors.append(name+'：'+str(error))
    return values,errors


def begin(request,target,run_id):
    attempt = target/uuid.uuid4().hex;attempt.mkdir(parents=True,exist_ok=False)
    manifest = {'schema_version':'1.0','skill_version':'0.2.0-dev.1','task_id':'vllm-migration-execute',
        'run_id':run_id,'inputs':request,'input_identity':identity(request),'status':'running','started_at':now(),
        'tasks':{k:{'status':'not_run'} for k in ['precheck','adapt','register','verify']},'errors':[],
        'conclusions':{'preparation':'not_evaluated','migration':'not_evaluated','functional_validation':'not_evaluated'},
        'evidence':{'manifest':str(attempt/'migration-manifest.json'),'ledger':str(attempt/'task-ledger.json'),
                    'handoff':str(attempt/'migration-handoff.json')},
        'uncovered':['不执行正式精度评分、性能验收、多机调度或自动异常修复'], 'next_action':'running'}
    save(attempt,manifest)
    return attempt,manifest


def save(attempt,value):
    write(attempt/'task-ledger.json',value['tasks'])
    write(attempt/'migration-manifest.json',value)


def publish(attempt,value,status):
    if status=="blocked" and any(t["status"]=="failed" for t in value["tasks"].values()):status="failed"
    value['status'] = status;value['finished_at'] = now()
    passed = status=='completed'
    value['conclusions']['migration'] = 'pass' if passed else ('fail' if status=='failed' else 'review_required')
    value['conclusions']['functional_validation'] = 'pass' if passed else 'not_evaluated'
    if passed:value['next_action'] = 'complete'
    write(attempt/'task-ledger.json',value['tasks'])
    write(attempt/'migration-handoff.json',{'schema_version':'1.0','stage':ref(attempt/'task-ledger.json'),
          'route':value.get('route'),'conclusions':value['conclusions'],'tasks':value['tasks'],
          'deployment':value.get('deployment'),'uncovered':value['uncovered']})
    write(attempt/'migration-manifest.json',value)
    return {'completed':0,'blocked':3,'failed':1}[status],{'manifest':value['evidence']['manifest'],
            'status':status,'conclusions':value['conclusions'],'next_action':value['next_action']}


def finish(attempt,value,status):
    try:return publish(attempt,value,status)
    except KeyboardInterrupt as error:
        value['errors'].append(str(error) or '终态发布时取消');value['next_action']='cancelled'
        previous = {s:signal.signal(s,signal.SIG_IGN) for s in [signal.SIGINT,signal.SIGTERM]}
        try:return publish(attempt,value,'failed')
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)


def delegate(name,request,attempt,run_id,timeout,*,container=None,action=None):
    if container:
        code,text,normalized = transport.execute(container,request,attempt,run_id,timeout)
        return receipts.child(text,code,name,normalized,attempt/"children"/name,run_id)
    skill,entry = ENTRIES[name];cli = release_skill(skill)/'scripts'/entry
    children = attempt/'children'/name
    args = [sys.executable,'-B',str(cli),'--request',str(attempt/(name+'.request.json')),
            '--work-root',str(children),'--run-id',run_id]
    if action:args.extend(['--action',action])
    write(attempt/(name+'.request.json'),request,redact=False)
    code,text = call([*args,'--plan'],min(timeout,60),attempt,name+'-plan')
    normalized = receipts.plan(text,code);write(attempt/(name+'.request.json'),normalized,redact=False)
    code,text = call(args,timeout,attempt,name)
    value,reference = receipts.child(text,code,name,normalized,children,run_id)
    return value,reference


def step(name,request,attempt,manifest,timeout,*,container=None):
    item = manifest['tasks'][name];item['status'] = 'running';save(attempt,manifest)
    value,reference = delegate(name,request,attempt,manifest['run_id'],timeout,container=container)
    item.update(status=value['status'],receipt=reference,conclusions=value['conclusions'])
    manifest['uncovered'].extend(value['uncovered'])
    save(attempt,manifest)
    return value


def dependencies(manifest,reason):
    for item in manifest['tasks'].values():
        if item['status']=='running':item.update(status='failed',reason=reason)
        elif item['status']=='not_run':item.update(status='blocked',reason=reason)


def adapt_step(request,precheck,verify,manifest):
    item = manifest['tasks']['adapt']
    if 'adaptation' not in request:
        item.update(status='blocked',reason='需通过适配公开步骤完成源码及check交接')
        manifest['next_action'] = 'adaptation_required'
        return False
    value = binding.adaptation(request['adaptation'],precheck,verify,request.get('adaptation_roots',{}),bound(request['register']) if 'register' in request else {})
    item.update(status='completed',receipt=request['adaptation'],conclusions=value['conclusions'])
    return True


def registration_step(request,values,precheck,attempt,manifest):
    if 'register' not in values:
        manifest['next_action'] = 'registration_required';return False
    registration = values['register'];binding.registration(registration,precheck,values['verify'],bool(request.get('register_container')))
    # 容器不能读取宿主预检路径；阶段先绑定同一架构/config/版本，随后传递共同注册回执。
    if not request.get('register_container'):
        registration['precheck'] = {'manifest':manifest['tasks']['precheck']['receipt']['path'],
                                    'sha256':manifest['tasks']['precheck']['receipt']['sha256']}
    if request.get('register_container'):transport.inspect(request['register_container'],attempt)
    result = step('register',registration,attempt,manifest,request.get('timeout_seconds',14400),container=request.get('register_container'))
    if result['status']!='completed' or result['conclusions'].get('registration')!='pass':return False
    binding.carrier_request(request['register'],values['verify'],result)
    return True


def verify_step(request,values,assets,attempt,manifest):
    verify = values['verify'];handoffs = {};verify['handoffs'] = handoffs
    handoffs['prepare'] = request['preparation']
    for name,key in [('precheck','precheck'),('adapt','adaptation'),('register','registration')]:
        if manifest['tasks'][name].get('receipt'):handoffs[key] = manifest['tasks'][name]['receipt']
    manifest['images'] = {'prepared':assets['image']['image_id'],
        'requested':verify['service'].get('identity',verify['service'].get('docker',{})).get('image_id'),
        'build':bool(verify.get('build'))}
    result = step('verify',verify,attempt,manifest,request.get('timeout_seconds',14400))
    handoff = result['evidence'].get('handoff')
    if handoff:manifest['deployment'] = ref(handoff)
    elif result['status']=='completed':raise ValueError('验证未交付部署资源清单')
    status = receipts.outcome(result)
    if status=='awaiting_review':
        manifest['next_action']='semantic_review_required';return 'blocked'
    return status


def workflow(request,values,attempt,manifest):
    assets = binding.preparation(request['preparation'],values['precheck'],values['verify'])
    manifest['conclusions']['preparation'] = 'pass'
    precheck = step('precheck',values['precheck'],attempt,manifest,request.get('timeout_seconds',14400))
    route = binding.selected(precheck,values['verify']);manifest['route'] = route
    if precheck['status']!='completed' or route=='blocked':return 'blocked'
    if route=='adaptation_required':
        if not adapt_step(request,precheck,values['verify'],manifest):return 'blocked'
    else:manifest['tasks']['adapt'].update(status='skipped',reason='预检路线不要求实现适配')
    if route in ('registration_required','adaptation_required'):
        if not registration_step(request,values,precheck,attempt,manifest):return 'blocked'
    else:manifest['tasks']['register'].update(status='skipped',reason='预检已有映射；运行载体仍复验注册')
    return verify_step(request,values,assets,attempt,manifest)


def run(request,work_root,run_id):
    values,errors = preflight(request)
    target = output(work_root,run_id,roots(list(values.values())))
    attempt,manifest = begin(request,target,run_id);manifest['errors'] = errors
    status = 'blocked'
    try:
        if not errors:status = workflow(request,values,attempt,manifest)
    except EvidenceError:raise
    except (ValueError,KeyError,TypeError,RecursionError) as error:
        manifest['errors'].append(str(error));status = 'blocked'
    except (KeyboardInterrupt,OSError,RuntimeError) as error:
        manifest['errors'].append(str(error) or '阶段被取消');status = 'failed'
    if manifest['next_action']=='running':manifest['next_action']='resolve_blockers'
    if status!='completed':dependencies(manifest,'必需前置项未完成，查看已执行回执及errors')
    return finish(attempt,manifest,status)


def finalize(request,work_root,run_id):
    source = receipts.common(bound(request['source']),'vllm-migration-execute')
    if source['status']!='blocked' or source.get('next_action')!='semantic_review_required':raise ValueError('仅收敛待语义复核的阶段')
    raw = source['tasks']['verify']['receipt'];original = bound(raw)
    review = receipts.common(bound(request['review']),'verify-agent')
    if review.get('operation')!='review' or review['inputs'].get('source')!=raw:raise ValueError('review未绑定本阶段验证报告')
    if not receipts.verification(review):raise ValueError('review仍未通过全部必需结论')
    if review['evidence'].get('handoff')!=original['evidence'].get('handoff'):raise ValueError('review部署交接不一致')
    values,errors = preflight(source['inputs'])
    if errors:raise ValueError('原阶段输入引用已变化')
    recheck_stage(source,original,values)
    target = output(work_root,run_id,roots(list(values.values())))
    attempt,manifest = begin(request,target,run_id)
    status='completed'
    try:
        for key in ['route','tasks','images','deployment','uncovered']:manifest[key] = source.get(key)
        if manifest['deployment']:bound(manifest['deployment'])
        validated,validated_ref = delegate('verify',review['inputs'],attempt,run_id,60,action='review')
        if not receipts.verification(validated):raise ValueError('原始验证证据复验未通过')
        manifest['review'] = request['review'];manifest['review_revalidated'] = validated_ref
        manifest['conclusions']['preparation'] = 'pass'
        manifest['tasks']['verify'] = {**manifest['tasks']['verify'],'review':request['review'],'conclusions':review['conclusions']}
    except EvidenceError:raise
    except (KeyboardInterrupt,OSError,ValueError,KeyError,TypeError,RuntimeError,RecursionError) as error:
        manifest['errors'].append(str(error) or '复核时取消');manifest['next_action']='resolve_review_failure';status='failed'
    return finish(attempt,manifest,status)


def recheck_stage(source,original,values):
    if source['conclusions'].get('preparation')!='pass' or receipts.outcome(original)!='awaiting_review':
        raise ValueError('原阶段仍有非语义必需检查未通过')
    binding.preparation(source['inputs']['preparation'],values['precheck'],values['verify'])
    precheck = bound(source['tasks']['precheck']['receipt'])
    if binding.selected(precheck,values['verify'])!=source['route']:raise ValueError('原阶段路线不一致')
    if source['route']=='adaptation_required':
        binding.adaptation(source['inputs']['adaptation'],precheck,values['verify'],source['inputs'].get('adaptation_roots',{}),values.get('register',{}))
    if source['route'] in ('registration_required','adaptation_required'):
        registered = receipts.completed(source['tasks']['register']['receipt'],'vllm-model-register','registration')
        binding.carrier_request(source['inputs']['register'],values['verify'],registered)
