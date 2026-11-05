"""测量、分析及可选基线的失败边界与报告聚合。"""
from pathlib import Path
import subprocess

from .comparison import judge
from . import backends, preflight
from .contract import check_output
from .evidence import begin, bound, finish, record, verify_files, write
from .history import details as history_details, native, reanalyze
from .inputs import collect
from .backends import read_task
from .runtime import aisbench, service


def safe_parent(parent, protected):
    check_output(parent,protected)


def failure(manifest,error,status='failed'):
    manifest['diagnostic']={'type':type(error).__name__,'message':str(error)}
    for entry in manifest['detailed_report'].get('ledger',[]):
        if entry['status']=='running':entry.update(status='failed',reason='必需执行失败或取消')
        elif entry['status']=='pending':entry.update(status='not_run',reason='执行在该项开始前终止')
    manifest['conclusions']['evaluation_validity']='fail' if status=='failed' else 'not_evaluated'
    return finish(manifest,status)



def baseline(request,parent,run_id,manifest):
    spec=request.get('baseline')
    if spec is None:return None,None
    try:
        if 'report' in spec:
            report=bound(spec['report'])
        else:
            from .contract import validate
            value=bound(spec['request']);validate(value,'run')
            if 'baseline' in value:raise ValueError('基线请求不得递归引用基线')
            _,report=run(value,parent,run_id+'-baseline',baseline_mode=True)
        rows=reanalyze(report)
        if report['detailed_report'].get('hardware')!='gpu':
            raise ValueError('基线来源未声明并绑定 GPU 测量，不能形成 GPU/NPU 对齐结论')
        if report['status']!='completed' or report['conclusions']['evaluation_validity']!='pass':
            raise ValueError('基线评测尚未取得完整有效结果')
        manifest['evidence']['baseline']=report['report_path']
        return rows,report['detailed_report']['conditions']
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        manifest['detailed_report']['baseline_error']={'type':type(error).__name__,'message':str(error)}
        manifest['uncovered'].append('GPU 基线不可用；保留单端测量，不形成对齐结论')
        return [],None


def conclude(request,parent,run_id,manifest,current,conditions):
    other,other_conditions=baseline(request,parent,run_id,manifest)
    comparable=backends.comparable_conditions(conditions)==backends.comparable_conditions(other_conditions)
    judgment=judge(current,other,request.get('targets',[]),comparable)
    manifest['conclusions'].update({k:judgment[k] for k in ('comparability','precision_target')})
    manifest['detailed_report']['comparison']=judgment
    manifest['detailed_report']['accuracy_evaluation']={'tasks':current}
    manifest['accuracy_evaluation']=manifest['detailed_report']['accuracy_evaluation']
    if not request.get('baseline'):manifest['uncovered'].append('GPU 基线缺失，未验证 GPU/NPU 精度对齐')
    if not request.get('targets'):manifest['uncovered'].append('精度目标缺失，未判定模型达标')


def measure_tasks(request,prepared,attempt,manifest):
    values=[]
    entries=[{'step':task['dataset'],'status':'pending'} for task in prepared['tasks']]
    manifest['detailed_report']['ledger']=entries
    bind_outputs(prepared,values,manifest)
    for task,entry in zip(prepared['tasks'],entries):
        entry.update(status='running', phase='starting_evaluator',
                     progress=str(Path(task['output_root'])/'http-capture/progress.json'))
        write(manifest['report_path'],manifest)
        try:
            process=backends.adapter(request).execute(request,task,attempt)
            measured=read_task(task['output_root'],task)
            measured['process']=process
            if process['returncode']!=0:
                measured['valid']=False;measured['errors'].append('评测子进程执行失败')
            values.append(measured)
            entry['status']='succeeded' if measured['valid'] else 'failed'
        except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.SubprocessError) as error:
            entry.update(status='failed',diagnostic={'type':type(error).__name__,'message':str(error)})
            values.append({'dataset':task['dataset'],'valid':False,'metrics':{},'errors':[str(error)],'files':{}})
        bind_outputs(prepared,values,manifest)
        write(manifest['report_path'],manifest)
    return values


def bind_outputs(prepared,values,manifest):
    files=dict(prepared['files'])
    for row in values:
        for path,expected in row['files'].items():files[str(Path(row['output_root'])/path)]=expected
    manifest['detailed_report'].update(tasks=prepared['tasks'],measurements=values,
                                      conditions=prepared['conditions'],files=files)
    manifest['accuracy_evaluation']={'tasks':values}
    manifest['detailed_report']['accuracy_evaluation']=manifest['accuracy_evaluation']
    complete=len(values)==len(prepared['tasks']) and bool(values)
    manifest['conclusions']['evaluation_validity']='pass' if complete and all(x['valid'] for x in values) else 'fail'


def run(request,parent,run_id,baseline_mode=False):
    inputs=None
    try:
        inputs=collect(request,baseline_mode)
        safe_parent(parent,inputs['protected'])
    except (OSError,ValueError,KeyError,TypeError) as error:
        manifest=record(request,run_id,'run','blocked');manifest['summary']=str(error)
        return 3,manifest
    attempt,manifest=begin(parent,request,run_id,'run')
    manifest['detailed_report']['ledger']=[]
    try:
        manifest['detailed_report']['protected_roots']=inputs['protected']
        manifest['detailed_report']['hardware']=inputs['hardware']
        preflight.before_service(request,inputs,attempt,manifest)
        before=service.snapshot(inputs,request,attempt,'before')
        inputs['service_snapshot']=before
        prepared=prepare(request,inputs,attempt)
        values=measure_tasks(request,prepared,attempt,manifest)
        bind_outputs(prepared,values,manifest)
        verify_files(prepared.get('validation_files',{}))
        after=service.snapshot(inputs,request,attempt,'after')
        if before!=after:raise ValueError('评测期间服务或 worker 身份变化')
        collect(request,baseline_mode)
        manifest['detailed_report']['context_validation']='pass'
        manifest['environment_check']={'success':True,'service_identity_unchanged':True}
        conclude(request,parent,run_id,manifest,values,prepared['conditions'])
        status='completed' if all(x['valid'] for x in values) else 'failed'
        return finish(manifest,status)
    except (KeyboardInterrupt,SystemExit) as error:
        return failure(manifest,error)
    except backends.PreparationBlocked as error:
        return failure(manifest,error,'blocked')
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,TimeoutError,subprocess.SubprocessError) as error:
        return failure(manifest,error)


def analyze(request,parent,run_id):
    if 'request' in request.get('baseline',{}):raise ValueError('分析不能发起基线测量')
    source=bound(request['source'])
    protected=[Path(request['source']['path'])]
    details=history_details(source)
    if source.get('task_id')=='verify-agent-plus':protected.append(Path(request['source']['path']).parent)
    protected.extend(row['output_root'] for row in details.get('tasks',[]) if isinstance(row,dict) and 'output_root' in row)
    protected.extend(source.get('detailed_report',{}).get('protected_roots',[]))
    safe_parent(parent,protected)
    attempt,manifest=begin(parent,request,run_id,'analyze')
    try:
        if source.get('format') in backends.NATIVE_FORMATS:
            return import_native(source,request,parent,run_id,manifest)
        values=reanalyze(source)
        details=source['detailed_report']
        manifest['detailed_report'].update(tasks=details['tasks'],measurements=values,
                                          conditions=details['conditions'],files=details['files'],hardware=details.get('hardware'),
                                          context_validation=details.get('context_validation','not_evaluated'))
        manifest['evidence']['source']=request['source']['path']
        manifest['uncovered'].append('本次仅分析固定历史产物，不代表本次服务或 NPU 运行')
        manifest['conclusions']['evaluation_validity']='pass' if all(x['valid'] for x in values) else 'fail'
        conclude(request,parent,run_id,manifest,values,details['conditions'])
        return finish(manifest,'completed' if all(x['valid'] for x in values) else 'failed')
    except (OSError,ValueError,KeyError,TypeError) as error:
        return failure(manifest,error)


def prepare(request,inputs,attempt):
    try:
        return backends.bind(backends.adapter(request).prepare(request,inputs,attempt),request)
    except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.SubprocessError) as error:
        raise backends.PreparationBlocked(str(error)) from error


def import_native(source,request,parent,run_id,manifest):
    value=native(source);validity=value.pop('validity')
    manifest['detailed_report'].update(value)
    manifest['accuracy_evaluation']={'tasks':value['measurements']}
    manifest['detailed_report']['accuracy_evaluation']=manifest['accuracy_evaluation']
    manifest['conclusions']['evaluation_validity']=validity
    manifest['uncovered'].append('导入固定历史产物；本次未运行推理，不重新证明历史GPU运行')
    if validity!='pass':
        manifest['uncovered'].append('原生产物缺少完整HTTP、执行回执或配置身份，或存在无效结果')
        return finish(manifest,'failed' if validity=='fail' else 'blocked')
    conclude(request,parent,run_id,manifest,value['measurements'],value['conditions'])
    return finish(manifest,'completed')
