"""只读核对已有服务、监听者及 worker；不创建或停止服务。"""
import http.client
import json
import os
import re
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from ..evidence import write
from ..evidence import sha256
from . import device_check


def pid_identity(expected):
    data=Path('/proc',str(expected['pid']),'stat').read_text().rsplit(')',1)[1].split()
    actual={'pid':expected['pid'],'start_ticks':data[19],'pgid':int(data[2])}
    if any(actual[k]!=expected[k] for k in actual):raise ValueError('服务 PID/启动身份已经变化')
    return actual


def resource_identity(resource):
    expected=resource['identity']
    if resource['kind']=='process':return pid_identity(expected)
    if resource['kind']!='docker':raise ValueError('不支持的已有服务载体')
    raw=subprocess.check_output(['docker','inspect',expected['container_id']],timeout=15)
    values=json.loads(raw)
    if not isinstance(values,list) or len(values)!=1:raise ValueError('容器身份回执无效')
    item=values[0]
    actual={'container_id':item['Id'],'image_id':item['Image'],'started_at':item['State']['StartedAt']}
    if not item['State']['Running'] or any(actual[k]!=expected[k] for k in actual):
        raise ValueError('部署容器身份已经变化或不再运行')
    return actual


def service_endpoint(value):
    from service_address import endpoint, assert_local
    parsed = endpoint(value, schemes=('http',))
    assert_local(parsed.hostname)
    return parsed


def model_card(handoff):
    endpoint=service_endpoint(handoff['endpoint'])
    body=json_get(endpoint,'/v1/models')
    version=json_get(endpoint,'/version').get('version')
    if not isinstance(version,str) or not version:
        raise ValueError('无法核对实际 API 版本')
    entries=body.get('data',[])
    if not isinstance(entries,list) or any(not isinstance(v,dict) for v in entries):raise ValueError('模型卡列表无效')
    cards=[v for v in entries if v.get('id')==handoff['model']['name']]
    if len(cards)!=1 or cards[0].get('root')!=handoff['model']['runtime_root']:
        raise ValueError('实际服务模型名称或权重根与交接不一致')
    limit=cards[0].get('max_model_len')
    if type(limit)!=int or limit<=0:raise ValueError('未取得实际服务上下文上限')
    return {'id':cards[0]['id'],'root':cards[0]['root'],'max_model_len':limit,'version':version}


def legacy(request,handoff):
    value=request.get('_legacy_constraints',{})
    expected={'model_path':handoff['model']['root'],'model_name':handoff['model']['name'],
              'port':urlsplit(handoff['endpoint']).port,'host':urlsplit(handoff['endpoint']).hostname}
    for key,item in value.items():
        if key=='dataset':
            if len(request['tasks'])!=1 or item!=request['tasks'][0]['dataset']:raise ValueError('旧数据集参数与精度请求不一致')
        elif key not in expected:raise ValueError('旧部署参数须委托部署任务，不能由精度任务覆盖')
        elif item!=expected[key]:raise ValueError('旧参数与精度请求不一致：'+key)


def snapshot(inputs,request,attempt,label):
    handoff=inputs['handoff'];legacy(request,handoff)
    resource=resource_identity(handoff['resource'])
    workers=handoff['workers']
    identities=[pid_identity(value) for value in workers['listener_identities']]
    identities.extend(pid_identity(value['identity']) for value in workers.get('npu_processes',[]))
    identities.extend(pid_identity(value['identity']) for value in workers.get('gpu_processes',[]))
    if not identities:raise ValueError('缺少可核验的API/worker身份')
    for value in identities:assert_owner(value['pid'],handoff['resource'])
    check_listener(handoff)
    result={'resource':resource,'workers':identities,'model':model_card(handoff),
            'generation':observed_generation(workers),'installed_versions':runtime_metadata(handoff)}
    if inputs.get('hardware')=='gpu':result['gpu_processes']=gpu_evidence(handoff)
    else:result.update(device_check.inspect(inputs,attempt,label))
    write(attempt/('service-'+label+'.json'),result)
    return result


def gpu_evidence(handoff):
    raw=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'],text=True,timeout=15)
    rows=[]
    for line in raw.splitlines():
        pid,uuid=line.split(',',1)
        rows.append({'pid':int(pid.strip()),'gpu_uuid':uuid.strip()})
    expected={row['pid'] for row in handoff['workers']['gpu_processes']}
    if not expected.issubset({row['pid'] for row in rows}):raise ValueError('没有观察到声明的GPU计算进程')
    return [row for row in rows if row['pid'] in expected]


GENERATION_FLAGS={'--config','--generation-config','--override-generation-config','--chat-template',
                  '--seed','--structured-outputs-config','--logits-processors','--logits-processor-pattern',
                  '--chat-template-content-format','--default-chat-template-kwargs','--reasoning-parser',
                  '--enable-reasoning','--enable-auto-tool-choice','--tool-call-parser','--response-role',
                  '--dtype','--quantization','--tokenizer','--tokenizer-revision'}


def generation_parameters(argv):
    result={}
    for index,item in enumerate(argv):
        flag,separator,value=item.partition('=')
        if flag in GENERATION_FLAGS:
            result[flag]=value if separator else (argv[index+1] if index+1<len(argv) and not argv[index+1].startswith('--') else True)
    return result


def observed_generation(workers):
    commands=[]
    for expected in workers['listener_identities']:
        pid=expected['pid']
        argv=Path('/proc',str(pid),'cmdline').read_bytes().decode().strip('\0').split('\0')
        value=generation_parameters(argv)
        for flag in ('--config','--chat-template','--generation-config','--tokenizer'):
            path=value.get(flag)
            if isinstance(path,str):value[flag]=generation_file(pid,flag,path)
        commands.append(value)
    if not commands or any(x!=commands[0] for x in commands):raise ValueError('实际服务的生成参数来源不唯一')
    return commands[0]


def process_path(pid,value):
    root=Path('/proc',str(pid),'root' if Path(value).is_absolute() else 'cwd')
    return root/value.lstrip('/')


def generation_file(pid,flag,value):
    if flag=='--generation-config' and value in ('auto','vllm'):return value
    if flag=='--chat-template' and ('{{' in value or '{%' in value):return value
    path=process_path(pid,value)
    if path.is_dir() and flag=='--generation-config':path=path/'generation_config.json'
    if path.is_file():return {'sha256':sha256(path)}
    if path.is_dir() and flag=='--tokenizer':
        files={str(p.relative_to(path)):sha256(p) for p in sorted(path.rglob('*')) if p.is_file() and p.suffix!='.pyc'}
        if files:return {'files':files}
    raise ValueError('无法固定实际生成、模板或 tokenizer 文件内容')


def assert_owner(pid,resource):
    expected=resource['identity']
    if resource['kind']=='docker':
        if expected['container_id'] not in Path('/proc',str(pid),'cgroup').read_text():
            raise ValueError('实际进程不属于交接容器')
    elif os.getpgid(pid)!=expected['pgid']:raise ValueError('实际进程不属于交接进程组')


def listen_inodes(port):
    found=set()
    for name in ('tcp','tcp6'):
        for line in Path('/proc/net',name).read_text().splitlines()[1:]:
            fields=line.split()
            if fields[3]=='0A' and int(fields[1].split(':')[1],16)==port:found.add(fields[9])
    return found


def pid_sockets(pid):
    found=set()
    for path in Path('/proc',str(pid),'fd').iterdir():
        try:
            target=os.readlink(path)
            if target.startswith('socket:['):found.add(target[8:-1])
        except FileNotFoundError:continue
    return found


def check_listener(handoff):
    expected=listen_inodes(urlsplit(handoff['endpoint']).port)
    actual=set()
    for value in handoff['workers']['listener_identities']:actual.update(pid_sockets(value['pid']))
    if not expected or not expected.issubset(actual):raise ValueError('监听端口存在未交接的socket或已不属于原API进程')


def json_get(endpoint,route):
    connection=http.client.HTTPConnection(endpoint.hostname,endpoint.port,timeout=10)
    try:
        connection.request('GET',route)
        response=connection.getresponse();raw=response.read(2*1024*1024)
        if response.status!=200:raise ValueError('模型服务查询失败')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('服务回执不是对象')
        return value
    finally:connection.close()


def runtime_metadata(handoff):
    runtime=handoff['runtime'];versions=runtime['versions']
    code='import importlib.metadata as m,json; print(json.dumps({p:m.version(p) for p in '+repr(list(versions))+'}))'
    argv=[runtime['python'],'-B','-c',code]
    if handoff['resource']['kind']=='docker':
        argv=['docker','exec',handoff['resource']['identity']['container_id'],*argv]
    actual=json.loads(subprocess.check_output(argv,text=True,timeout=20))
    if actual!=versions:raise ValueError('运行载体安装版本与部署交接不一致')
    return actual
