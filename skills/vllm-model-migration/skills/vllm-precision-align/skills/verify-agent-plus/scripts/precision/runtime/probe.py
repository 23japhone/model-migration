"""在 AISBench 解释器内冻结实际配置和样本选择，不执行推理。"""
import ast
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

if __package__:
    from .sample_mapping import verify_samples
    from .versioning import inspect_environment
else:
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from sample_mapping import verify_samples
    from versioning import inspect_environment


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized(value):
    if isinstance(value,dict):return {str(k):normalized(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [normalized(v) for v in value]
    if isinstance(value,(str,int,float,bool)) or value is None:return value
    if hasattr(value,'__module__') and hasattr(value,'__qualname__'):
        return value.__module__+'.'+value.__qualname__
    raise ValueError('配置含不能固定身份的对象')


def indices(value,count):
    if value is None:return list(range(count))
    if not isinstance(value,str):raise ValueError('不支持的 reader test_range 形状')
    node=ast.parse('rows'+value,mode='eval').body
    if not isinstance(node,ast.Subscript) or not isinstance(node.slice,ast.Slice):
        raise ValueError('当前只核验明确的 reader 切片选择；不能猜测其他选择表达式')
    parts=[ast.literal_eval(x) if x is not None else None for x in (node.slice.lower,node.slice.upper,node.slice.step)]
    if any(x is not None and type(x)!=int for x in parts):raise ValueError('reader 切片无效')
    return list(range(count))[slice(*parts)]


def model_conditions(model):
    excluded={'model','abbr','path','host_ip','host_port','url','api_key'}
    return normalized({k:v for k,v in model.items() if k not in excluded})


def check_model(model,handoff):
    if model.get('attr')!='service' or model.get('model')!=handoff['model']['name']:
        raise ValueError('必须显式指定已交接的服务模型名')
    endpoint=urlsplit(handoff['endpoint'])
    url=model.get('url','')
    if url:
        if url.rstrip('/')!=handoff['endpoint'].rstrip('/'):
            raise ValueError('AISBench url 须为与交接一致的服务 base URL，不能重复附加 API 路由')
    elif model.get('host_ip')!=endpoint.hostname or model.get('host_port')!=endpoint.port:
        raise ValueError('评测端点与部署交接不一致')
    if model.get('stream'):raise ValueError('当前只验收非流式生成精度，不静默关闭 stream')
    if model.get('api_key'):raise ValueError('本版仅验收无认证回环服务，不保存凭据配置')
    if model.get('generation_kwargs',{}).get('num_return_sequences',1)!=1:
        raise ValueError('本版仅核验每个样本一次生成，不自动改变生成次数')
    if type(model.get('max_out_len'))!=int or model['max_out_len']<=0:
        raise ValueError('必须显式配置有效 max_out_len')
    if type(model.get('retry'))!=int or model['retry']!=1:
        raise ValueError('本版需显式 retry=1：该版本将 retry 作为尝试总次数，禁止零请求或隐式重试')


def selected_file(cfg,asset):
    root=Path(asset['path']);path=Path(cfg.get('path',''))
    if not path.is_absolute() or not path.resolve().is_relative_to(root) or path.resolve()!=path:
        raise ValueError('实际数据配置必须使用已交接数据根内的绝对路径')
    records=asset['selection']['records'];files={x['file'] for x in records}
    if len(files)!=1:raise ValueError('当前选择含多个原文件，需要先提供可明确映射的独立评测任务')
    original=next(iter(files));row=next(x for x in asset['files'] if x['path']==original)
    if path.is_file() and path!=root/original:raise ValueError('实际 reader 数据文件与选择清单不同')
    return row,records


def check_selection(cfg,asset,task,build):
    row,records=selected_file(cfg,asset)
    reader=cfg.get('reader_cfg',{})
    if reader.get('test_split','test')!=asset['selection']['split']:
        raise ValueError('reader split 与选择清单不一致')
    chosen=indices(reader.get('test_range'),row['rows'])
    if chosen!=[x['row_index'] for x in records]:raise ValueError('实际 reader 样本范围与准备选择不一致')
    actual=build(cfg).test
    if len(actual)!=len(chosen) or task['sample_ids']!=list(range(len(actual))):
        raise ValueError('实际 reader 样本数或 AISBench 局部 id 不一致')
    if cfg.get('n',1)!=1 or cfg.get('k',1)!=1:
        raise ValueError('重复生成评测需要单独适配，不能套用单次覆盖规则')
    return verify_samples(cfg,asset,task,row,chosen,actual)



def distribution_sources(distribution):
    result={}
    for relative in distribution.files or []:
        if str(relative).startswith('ais_bench/') and str(relative).endswith('.py'):
            result[str(relative)]=digest(distribution.locate_file(relative))
    if not result:raise ValueError('无法固定 AISBench 实际源码内容')
    return result


def environment_identity():
    names={d.metadata['Name'].lower().replace('_','-') for d in importlib.metadata.distributions() if d.metadata.get('Name')}
    packages={name:importlib.metadata.version(name) for name in sorted(names)}
    distribution=importlib.metadata.distribution('ais-bench-benchmark')
    sources=distribution_sources(distribution)
    validation={str(Path(distribution.locate_file(relative)).resolve()):value for relative,value in sources.items()}
    return {'python':sys.version.split()[0],'packages':packages,'sources':sources},validation


def configuration_sources(value):
    identities={};files={}
    if isinstance(value,dict):values=value.values()
    elif isinstance(value,(list,tuple)):values=value
    else:
        name=getattr(value,'__module__',None)
        module=sys.modules.get(name)
        path=getattr(module,'__file__',None)
        if path:
            source=Path(path).resolve();identities[name]=digest(source);files[str(source)]=identities[name]
        return identities,files
    for item in values:
        names,paths=configuration_sources(item);identities.update(names);files.update(paths)
    return identities,files


def freeze(payload,output):
    request=payload['request'];ev=request['evaluator'];source=Path(ev['config']['path'])
    version_check=inspect_environment('aisbench',ev,payload['attempt'])
    if digest(source)!=ev['config']['sha256']:raise ValueError('配置在探测前发生变化')
    from mmengine.config import Config
    from ais_bench.benchmark.utils.config import build_dataset_from_cfg
    cfg=Config.fromfile(str(source))
    models=cfg.get('models',[]);datasets=cfg.get('datasets',[])
    if len(models)!=1:raise ValueError('一个测量请求须对应一个明确服务，基线使用独立请求')
    check_model(models[0],payload['handoff'])
    expected={x['dataset']:x for x in request['tasks']}
    if len(datasets)!=len(expected) or {x.get('abbr') for x in datasets}!=set(expected):
        raise ValueError('配置实际数据任务与请求不一致')
    attempt=Path(payload['attempt']);tasks=[];conditions=[]
    files={str(path):digest(path) for path in attempt.glob('evaluator-*.json')}
    archive=attempt/'source-config.py';archive.write_bytes(source.read_bytes());files[str(archive)]=digest(archive)
    for dataset in datasets:
        task=expected[dataset['abbr']]
        if models[0]['abbr']!=task['model_abbr']:raise ValueError('实际 model abbr 与请求不一致')
        sample_digest=check_selection(dataset,payload['asset'],task,build_dataset_from_cfg)
        single=copy.deepcopy(cfg);single['datasets']=[dataset]
        single['models'][0]['api_key']=''
        path=attempt/('frozen-'+dataset['abbr']+'.py');single.dump(str(path))
        files[str(path)]=digest(path)
        tasks.append({**task,'config':str(path),'output_root':str(attempt/'tasks'/dataset['abbr']),
                      'capture_required':True,'endpoint':payload['handoff']['endpoint'],
                      'served_model':payload['handoff']['model']['name'],
                      'max_model_len':payload['service_snapshot']['model']['max_model_len']})
        conditions.append({'config':normalized({k:v for k,v in dataset.items() if k not in ('path','abbr')}),
                           'reader_samples_sha256':sample_digest})
    identity=payload['identity']
    environment,validation=environment_identity()
    modules,module_files=configuration_sources(cfg.to_dict())
    environment['configuration_sources']=modules;validation.update(module_files)
    envpath=attempt/'evaluator-identity.json';envpath.write_text(json.dumps(environment,ensure_ascii=False,indent=2)+'\n')
    files[str(envpath)]=digest(envpath)
    protocol={'model_files':identity['model_files'],'weight_files':identity['weight_files'],
              'dataset':identity['dataset'],'selection':identity['selection'],
              'datasets':conditions,'model':model_conditions(models[0]),'evaluator_version':version_check['installed_version'],
              'evaluator_identity':environment,
              'server_generation_files':identity['server_generation_files'],
              'server_generation':payload.get('service_snapshot',{}).get('generation'),
              'max_model_len':payload['service_snapshot']['model']['max_model_len'],
              'vllm_version':payload.get('service_snapshot',{}).get('model',{}).get('version')}
    value={'tasks':tasks,'conditions':protocol,'files':files,'validation_files':validation,'server_context':identity,
           'environment':{'python':sys.version,'executable':sys.executable,
                          'version':version_check['installed_version'],'version_check':version_check}}
    Path(output).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


if __name__=='__main__':
    freeze(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2])
