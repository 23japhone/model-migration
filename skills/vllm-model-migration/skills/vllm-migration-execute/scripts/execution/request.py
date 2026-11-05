"""纯输入契约与安装/资产目录保护。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import re



def fields(value,required,optional=()):
    if not isinstance(value,dict) or set(required)-value.keys() or value.keys()-set(required)-set(optional):
        raise ValueError('对象字段缺失或存在未知字段')


def absolute(value):
    if not isinstance(value,str) or '\0' in value or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('需要规范绝对路径')


def reference(value):
    fields(value,['path','sha256']);absolute(value['path'])
    if not isinstance(value['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',value['sha256']):raise ValueError('SHA256 无效')


def validate(value,action):
    if action == 'finalize':
        fields(value,['source','review'])
        for item in value.values():reference(item)
    else:
        fields(value,['schema_version','preparation','precheck','verify'],['register','adaptation','timeout_seconds','register_container','adaptation_roots'])
        if value['schema_version'] != '1.0':raise ValueError('仅支持 schema_version=1.0')
        for key in ['preparation','precheck','verify','register','adaptation']:
            if key in value:reference(value[key])
        if 'register_container' in value:container(value['register_container'])
        if 'adaptation_roots' in value:
            if not isinstance(value['adaptation_roots'],dict):raise ValueError('adaptation_roots 须为对象')
            for path in value['adaptation_roots'].values():absolute(path)
        timeout = value.get('timeout_seconds',14400)
        if type(timeout) is not int or not 1 <= timeout <= 86400:raise ValueError('超时须为1—86400秒')
    return value


def roots(values):
    result = []
    for value in values:
        for key in ['root','path','context','baseline','working','project_root','install_site']:
            if isinstance(value.get(key),str) and value[key].startswith('/'):result.append(Path(value[key]))
        for item in value.values():
            if isinstance(item,dict):result.extend(roots([item]))
    return result


def output(work_root,run_id,protected=()):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}',run_id):raise ValueError('run-id 无效')
    target = Path(work_root).expanduser().absolute()/run_id/'vllm-migration-execute'
    if target.resolve() != target:raise ValueError('输出不能经符号链接重定向')
    installed = release_root()
    for root in [installed,*protected]:
        root = Path(root).resolve()
        if target.is_relative_to(root) or root.is_relative_to(target):raise ValueError('运行目录必须位于安装包和资产之外')
    return target


def container(value):
    fields(value,['container_id','image_id','started_at','python'])
    if not re.fullmatch('[a-f0-9]{64}',value['container_id']):raise ValueError('需要完整CID')
    if not re.fullmatch('sha256:[a-f0-9]{64}',value['image_id']):raise ValueError('需要完整image ID')
    if not isinstance(value['started_at'],str) or not value['started_at']:raise ValueError('缺少StartedAt')
    absolute(value['python'])
