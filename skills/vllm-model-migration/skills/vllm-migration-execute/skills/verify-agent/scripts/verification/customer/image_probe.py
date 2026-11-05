"""无 NPU、无模型的短容器读取安装包身份；不修改基础镜像。"""
import json
import hashlib
from pathlib import Path
import re
import uuid

from ..runtime.command import run
from ..runtime import docker
from ..evidence import write_json
from .inspection import local_daemon

PROBE = '''import importlib.metadata as m,importlib.util,importlib.machinery,json,os,sys
from pathlib import Path
from urllib.parse import urlsplit,unquote

def module_origin(module):
 spec=importlib.util.find_spec(module)
 if spec is None or not spec.origin or not isinstance(spec.loader,importlib.machinery.SourceFileLoader):
  raise ValueError(module+": 无法定位实际存在的普通 Python 包")
 origin=Path(spec.origin).resolve()
 locations=list(spec.submodule_search_locations or [])
 if len(locations)!=1:
  raise ValueError(module+": 包目录缺失或存在多个候选")
 if origin.name!="__init__.py" or not origin.is_file() or Path(locations[0]).resolve()!=origin.parent:
  raise ValueError(module+": 模块位置不是唯一的普通 Python 包目录")
 return origin

def editable_root(distribution):
 raw=distribution.read_text("direct_url.json")
 if raw is None:
  raise ValueError("安装清单未记录源码且缺少 editable 来源")
 try:
  data=json.loads(raw)
 except (ValueError,TypeError) as exc:
  raise ValueError("editable 来源元数据损坏") from exc
 if not isinstance(data,dict) or not isinstance(data.get("dir_info"),dict) or data["dir_info"].get("editable") is not True:
  raise ValueError("安装清单未记录源码且来源未声明 editable=true")
 if not isinstance(data.get("url"),str):
  raise ValueError("editable 来源缺少本地目录 URL")
 url=urlsplit(data["url"])
 if url.scheme!="file" or url.netloc.lower() not in ("","localhost") or url.query or url.fragment:
  raise ValueError("editable 来源必须是本地目录 URL")
 root=Path(unquote(url.path,errors="strict"))
 if not root.is_absolute() or not root.is_dir():
  raise ValueError("editable 来源目录不存在或不是绝对路径")
 return root.resolve()

def package_root(distribution,module):
 entries=[p for p in (distribution.files or []) if str(p)==module+"/__init__.py"]
 if len(entries)>1:
  raise ValueError(module+": 安装清单存在多个候选")
 origin=module_origin(module)
 if entries:
  if Path(distribution.locate_file(entries[0])).resolve()!=origin:
   raise ValueError(module+": 源码位置与安装记录冲突，可能被其他模块遮蔽")
 else:
  root=editable_root(distribution)
  if root not in origin.parents:
   raise ValueError(module+": 实际模块位于 editable 来源目录之外")
 return origin.parent

result={"python":sys.executable,"versions":{},"sources":{},"env":{k:os.environ[k] for k in ("ASCEND_HOME_PATH","ASCEND_TOOLKIT_HOME") if k in os.environ}}
for package,module in (("vllm","vllm"),("vllm-ascend","vllm_ascend")):
 d=m.distribution(package);result["versions"][package]=d.version
 root=package_root(d,module)
 result["sources"][module]={"path":str(root),"distribution":package,"version":d.version}
print(json.dumps(result))
'''


def inspect(request, attempt):
    image = request['image_id']
    if not re.fullmatch('sha256:[a-f0-9]{64}', image):
        raise ValueError('镜像探测须使用实际 image ID')
    local_daemon(attempt)
    python = request.get('python', 'python3')
    nonce = uuid.uuid4().hex
    cid = run(['docker', 'create', '--network', 'none', '--read-only', '--label', 'vllm.verify.probe=' + nonce,
               '--entrypoint', python, image, '-B', '-c', PROBE], attempt / 'probe-create', timeout=60).strip()
    write_json(attempt / 'probe-resource.json', {'container_id': cid, 'image_id': image, 'nonce': nonce})
    try:
        result = json.loads(run(['docker', 'start', '-a', cid], attempt / 'probe-run', timeout=60))
        for module, source in result['sources'].items():
            parent = attempt / 'sources' / module
            parent.mkdir(parents=True)
            run(['docker', 'cp', cid + ':' + source['path'], str(parent / module)],
                attempt / ('copy-' + module), timeout=120)
            source['runtime_path'] = source.pop('path')
            source['path'] = str(parent)
            source['kind'] = 'installed_package'
            source['image_id'] = image
            source['expected_identity'] = source_identity(parent)
        result['image_id'] = image
        write_json(attempt / 'image-observation.json', result)
        return result
    finally:
        current = docker.inspect(cid, attempt, 'probe-cleanup-identity')
        if current['image_id'] != image or current['labels'].get('vllm.verify.probe') != nonce:
            raise ValueError('探测容器身份变化，不能清理未知资源')
        run(['docker', 'rm', '--force', cid], attempt / 'probe-remove', timeout=60)


def source_identity(root):
    rows = []
    for path in sorted(root.rglob('*.py')):
        if any(part in {'.git', '__pycache__', '.venv'} for part in path.relative_to(root).parts):
            continue
        if path.resolve() != path or len(rows) >= 10000:
            raise ValueError('安装源码快照包含链接或文件过多')
        raw = path.read_bytes()
        rows.append({'path': path.relative_to(root).as_posix(), 'size': len(raw),
                     'sha256': hashlib.sha256(raw).hexdigest()})
    if not rows:
        raise ValueError('安装源码快照没有 Python 源文件')
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()
