"""部署、模型和数据身份绑定；不承担服务启动或下载。"""
import hashlib
import json
from pathlib import Path

from .evidence import bound, sha256

VERIFY_REQUIRED = {'inputs','runtime','registration','workers','functional_protocol','semantic','lifecycle'}


def canonical(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def gpu_handoff(report):
    if report.get('schema_version')!='1.0':raise ValueError('GPU 基线交接版本无效')
    handoff=report['handoff']
    if handoff.get('final_service',{}).get('state')!='retained' or not handoff.get('workers',{}).get('gpu_processes'):
        raise ValueError('GPU 基线必须交接已有服务和明确计算进程')
    raw={'inputs':{'model':handoff['model'],'service':{'endpoint':handoff['endpoint']}}}
    return report,raw,handoff


def deployment(reference,baseline_mode=False):
    report=bound(reference)
    if baseline_mode and report.get('format')=='gpu-baseline-service-v1':return gpu_handoff(report)
    if (report.get('task_id')!='verify-agent' or report.get('status')!='completed' or report.get('success') is not True
            or any(report.get('conclusions',{}).get(k)!='pass' for k in VERIFY_REQUIRED)):
        raise ValueError('缺少已完成且全部必需功能通过的部署交接')
    raw=report
    if report.get('operation')=='review':
        raw=bound(report['inputs']['source'])
        if (raw.get('task_id')!='verify-agent' or raw.get('status')!='completed'
                or report['evidence']['handoff']!=raw['evidence']['handoff']):
            raise ValueError('部署复核来源或交接不一致')
    handoff=bound({'path':raw['evidence']['handoff'],'sha256':raw['detailed_report']['handoff_sha256']})
    if handoff.get('final_service',{}).get('state')!='retained':raise ValueError('部署服务未保留')
    if handoff['model']!=raw['inputs']['model'] or handoff['endpoint']!=raw['inputs']['service']['endpoint']:
        raise ValueError('部署交接与原始模型/端点不一致')
    return report,raw,handoff


def checked_file(root, relative, expected):
    path=Path(root)/relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.resolve()!=path or not path.is_relative_to(root):
        raise ValueError('资产路径越界或经过符号链接')
    if sha256(path)!=expected:raise ValueError('实际资产内容与交接不一致：'+relative)


def checked_model_file(root, relative, expected):
    from model_cache import checked
    path = checked(root, relative)
    if sha256(path) != expected:
        raise ValueError('实际模型内容与交接不一致：' + relative)


def dataset_files(asset):
    root=Path(asset['path'])
    if not root.is_absolute() or root.resolve()!=root:raise ValueError('数据根必须为实际绝对路径')
    files=asset['files']
    if not files or len({x['path'] for x in files})!=len(files):raise ValueError('数据文件清单为空或重复')
    for row in files:
        checked_file(root,row['path'],row['sha256'])
        if (root/row['path']).stat().st_size!=row['size']:raise ValueError('数据文件大小不一致')
    content=[{k:x[k] for k in ('path','size','sha256')} for x in sorted(files,key=lambda x:x['path'])]
    if asset['identity']!={'format':'vllm-dataset-file-list-v1','sha256':canonical(content)}:
        raise ValueError('数据内容身份无效')


def dataset_selection(asset):
    files=asset['files']
    selected=asset['selection'];records=selected['records']
    if selected['count']!=len(records) or not records or selected['sha256']!=canonical(records):
        raise ValueError('数据样本选择身份无效')
    if len({(x['file'],x['row_index']) for x in records})!=len(records):raise ValueError('样本选择重复')
    allowed={x['path'] for x in files}
    if any(x['file'] not in allowed or type(x['row_index'])!=int or x['row_index']<0 for x in records):
        raise ValueError('样本选择引用无效')


def dataset(reference):
    report=bound(reference)
    if (report.get('task_id')!='vllm-dataset-prepare' or report.get('status')!='completed'
            or report.get('conclusions',{}).get('assets')!='pass'):
        raise ValueError('数据准备交接尚未通过')
    asset=report['asset']
    dataset_files(asset);dataset_selection(asset)
    if asset.get('format')=='csv':asset['_csv_header']=report['inputs'].get('csv_header')
    return asset


def collect(request,baseline_mode=False):
    report,raw,handoff=deployment(request['deployment'],baseline_mode)
    hardware='gpu' if report.get('format')=='gpu-baseline-service-v1' else 'npu'
    if hardware=='npu' and not handoff['workers'].get('npu_processes'):
        raise ValueError('NPU 部署交接缺少实际计算进程')
    model=handoff['model'];root=Path(model['root'])
    if not root.is_absolute() or root.resolve()!=root:raise ValueError('模型根无效')
    if not model.get('weight_files') or set(model['weight_files'])-set(model['files']):
        raise ValueError('缺少固定完整权重身份')
    for relative,expected in model['files'].items():checked_model_file(root,relative,expected)
    config=request['evaluator']['config']
    if Path(config['path']).resolve()!=Path(config['path']) or sha256(config['path'])!=config['sha256']:
        raise ValueError('评测配置内容身份不一致')
    asset=dataset(request['dataset'])
    protected=[root,Path(asset['path']),Path(config['path']),
               Path(request['deployment']['path']),Path(request['dataset']['path'])]
    identity={'model_files':model['files'],'weight_files':sorted(model['weight_files']),
              'dataset':asset['identity'],'selection':asset['selection']['sha256'],
              'server_generation_files':{k:v for k,v in model['files'].items() if 'generation_config' in k or 'tokenizer' in k},
              'server_command':raw['inputs']['service'].get('command'),
              'framework':handoff.get('runtime',{}).get('versions',{})}
    return {'handoff':handoff,'asset':asset,'identity':identity,'hardware':hardware,'protected':list(map(str,protected)),
            'deployment_report':report,'deployment_raw':raw}
