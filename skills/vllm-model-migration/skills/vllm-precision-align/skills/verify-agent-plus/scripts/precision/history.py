"""历史精度产物的只读契约、执行回执与来源闭合检查。"""
from pathlib import Path

from .contract import absolute, task
from .evidence import bound, verify_files
from .backends import read_task, source_backend
from . import backends


def tasks(value):
    if not isinstance(value,list) or not value:raise ValueError('缺少历史评测任务')
    for item in value:
        fields=('dataset','model_abbr','sample_ids','metrics','source_columns','benchmark','subset')
        task({key:item[key] for key in fields if key in item},item.get('backend','aisbench'))
        absolute(item['output_root'])
    if len({item['dataset'] for item in value})!=len(value):raise ValueError('历史评测任务重复')
    return value


def details(source):
    value=source.get('detailed_report',source)
    if not isinstance(value,dict):raise ValueError('历史评测详情必须为对象')
    if 'tasks' in value:tasks(value['tasks'])
    roots=value.get('protected_roots',[])
    if not isinstance(roots,list):raise ValueError('历史保护根无效')
    for root in roots:absolute(root)
    return value


def closed_files(rows,files):
    for row in rows:
        observed={str(Path(row['output_root'])/key):value for key,value in row['files'].items()}
        if any(files.get(key)!=value for key,value in observed.items()):
            raise ValueError('原生产物未完整绑定读取的文件身份')


def reanalyze(source):
    if (source.get('schema_version')!='1.0' or source.get('task_id')!='verify-agent-plus'
            or source.get('operation') not in ('run','analyze')
            or source.get('status') not in ('completed','failed','blocked')):
        raise ValueError('来源不是精度测试报告')
    value=details(source);items=tasks(value['tasks']);verify_files(value['files'])
    backends.history_identity(value)
    if not isinstance(source.get('conclusions'),dict) or not isinstance(value.get('measurements'),list):
        raise ValueError('原始测量结论无效')
    original={row['dataset']:row for row in value['measurements']}
    context=source['conclusions'].get('evaluation_validity')=='pass' or value.get('context_validation')=='pass'
    rows=[read_task(item['output_root'],item) for item in items]
    closed_files(rows,value['files'])
    for row in rows:
        prior=original.get(row['dataset'],{});row['process']=prior.get('process')
        if not context or prior.get('valid') is not True or not process_succeeded(row['process']):
            row['valid']=False;row['errors'].append('原始执行未取得有效测量，不以重读文件改写失败')
    return rows


def process_succeeded(value):
    return (isinstance(value,dict) and type(value.get('returncode'))==int
            and value['returncode']==0 and not value.get('error'))


def execution(provenance,rows,files):
    references=provenance.get('execution',{});missing=False
    if not isinstance(references,dict):raise ValueError('历史执行回执索引无效')
    for row in rows:
        reference=references.get(row['dataset'])
        if reference is None:
            missing=True;continue
        process=bound(reference);files[reference['path']]=reference['sha256'];row['process']=process
        if not process_succeeded(process):
            row['valid']=False;row['errors'].append('原始评测执行回执未正常完成')
    return missing


def native(source):
    if source.get('schema_version')!='1.0':raise ValueError('原生产物导入版本无效')
    index=bound(source['config_index']);conditions=index['conditions']
    backend=source_backend(conditions,source.get('format'))
    items=tasks(source['tasks']);provenance=source['provenance']
    if not isinstance(provenance,dict):raise ValueError('原生产物来源无效')
    if items!=index['tasks']:raise ValueError('导入任务与实际配置索引不一致')
    if any(item.get('backend','aisbench')!=backend for item in items):
        raise ValueError('导入任务工具与配置身份不一致')
    verify_files(index['files']);verify_files(source['files'])
    rows=[read_task(item['output_root'],item) for item in items]
    files={**source['files'],**index['files'],source['config_index']['path']:source['config_index']['sha256']}
    closed_files(rows,files)
    missing=execution(provenance,rows,files)
    validity=native_validity(items,rows,conditions,missing)
    return {'tasks':items,'measurements':rows,'files':files,'conditions':conditions,
            'provenance':provenance,'hardware':provenance.get('accelerator'),'validity':validity}


def native_validity(items,rows,conditions,missing):
    if not all(row['valid'] for row in rows):return 'fail'
    complete=protocol_complete(conditions) and all(item.get('capture_required') for item in items)
    return 'pass' if complete and not missing else 'review_required'


def protocol_complete(conditions):
    if not backends.protocol_complete(conditions):return False
    fields={'model_files':dict,'weight_files':list,'dataset':dict,'selection':str,'datasets':list,
            'model':dict,'evaluator_identity':dict,'server_generation':dict,'max_model_len':int,'vllm_version':str}
    if any(type(conditions.get(key))!=kind for key,kind in fields.items()):return False
    nonempty=set(fields)-{'server_generation'}
    if not all(conditions[key] for key in nonempty) or conditions['max_model_len']<=0:return False
    env=conditions['evaluator_identity']
    if not all(env.get(key) for key in ('python','packages','sources','configuration_sources')):return False
    package='evalscope' if conditions.get('evaluator_backend')=='evalscope' else 'ais-bench-benchmark'
    if not isinstance(env['packages'],dict) or env['packages'].get(package)!=conditions.get('evaluator_version'):return False
    return all(isinstance(value,dict) and isinstance(value.get('reader_samples_sha256'),str)
               for value in conditions['datasets'])
