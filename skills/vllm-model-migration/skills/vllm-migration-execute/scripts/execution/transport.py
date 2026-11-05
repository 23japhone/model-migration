"""现有载体内注册的固定传输；不拥有服务生命周期。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, bundle_files, resource_snapshot, verify_snapshot
import json
from pathlib import Path
import shutil
import uuid

from .evidence import ref,write
from .process import call
from .receipts import plan,reply


def inspect(container,attempt):
    code,text = call(['docker','inspect',container['container_id']],30,attempt,'register-inspect-'+uuid.uuid4().hex)
    if code:raise ValueError('注册容器身份无法核对')
    values=json.loads(text)
    if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict):
        raise ValueError('容器身份回执必须为单项对象列表')
    item=values[0];state=item.get('State')
    if not isinstance(state,dict):raise ValueError('容器身份缺少State对象')
    if (item.get('Id')!=container['container_id'] or item.get('Image')!=container['image_id']
            or state.get('StartedAt')!=container['started_at'] or state.get('Running') is not True):
        raise ValueError('注册容器CID/image/StartedAt不符或已停止')


def transfer(argv,attempt,label):
    code,_ = call(argv,60,attempt,label)
    if code:raise RuntimeError('注册资源或证据复制失败：'+label)


def copy_resources(destination):
    root = release_root()
    paths = bundle_files()
    expected = resource_snapshot(paths)
    destination.mkdir()
    for path in paths:
        target = destination/path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    verify_snapshot(bundle_files(), expected)
    verify_snapshot(bundle_files(destination), expected, destination)
    return expected


def execute(container,request,attempt,run_id,timeout):
    inspect(container,attempt)
    local = attempt/'registration-resources'
    resources = copy_resources(local)
    write(attempt/'registration-resource-identity.json', resources)
    request_path = attempt/'register.request.json'
    write(request_path,request,redact=False)
    remote = '/tmp/vllm-migration-register-'+uuid.uuid4().hex
    cid = container['container_id']
    transfer(['docker','cp',str(local),cid+':'+remote],attempt,'register-copy')
    transfer(['docker','cp',str(request_path),cid+':'+remote+'-request.json'],attempt,'register-request-copy')
    entry = release_skill('vllm-model-register').relative_to(release_root())/'scripts/register.py'
    args = ['docker','exec',cid,container['python'],'-B',remote+'/'+entry.as_posix(),
            '--request',remote+'-request.json','--work-root',remote+'-runs','--run-id',run_id]
    code,text = call([*args,'--plan'],min(timeout,60),attempt,'register-plan')
    normalized = plan(text,code)
    code,text = call(args,timeout,attempt,'register')
    result = reply(text);path = Path(result['manifest'])
    if not path.is_relative_to(remote+'-runs'):raise ValueError('容器回执越过本次运行目录')
    copied = attempt/'children/register';copied.mkdir(parents=True,exist_ok=True)
    transfer(['docker','cp',cid+':'+remote+'-runs/.',str(copied)],attempt,'register-evidence-copy')
    inspect(container,attempt)
    result['manifest'] = str(copied/path.relative_to(remote+'-runs'))
    write(attempt/'register-origin.json',{'container':container,'remote_manifest':str(path),'local_receipt':ref(result['manifest'])})
    return code,json.dumps(result),normalized
