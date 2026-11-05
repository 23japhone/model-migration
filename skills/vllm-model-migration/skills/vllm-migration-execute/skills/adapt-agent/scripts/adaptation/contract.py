"""纯请求形状与输出保护；计划模式不读取目标资产。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path, PurePosixPath
import re

HASH = re.compile(r'[0-9a-f]{64}')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}')


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError('对象字段缺失或存在未知字段：' + ','.join(required))


def text(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:raise ValueError('需要非空文本')


def absolute(value):
    text(value)
    if not Path(value).is_absolute() or '..' in Path(value).parts:raise ValueError('需要无上行段的绝对路径')


def relative(value):
    text(value)
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or '\\' in value:
        raise ValueError('需要规范的根内相对路径')


def unique_strings(value):
    if not isinstance(value, list) or not value:raise ValueError('需要非空列表')
    for item in value:text(item)
    if len(set(value)) != len(value):raise ValueError('列表不能重复')


def reference(value):
    fields(value, ['path', 'sha256'])
    absolute(value['path'])
    if not isinstance(value['sha256'], str) or not HASH.fullmatch(value['sha256']):raise ValueError('SHA256 无效')


def model(value):
    fields(value, ['root', 'files', 'weight_files', 'config', 'architecture', 'modalities'])
    absolute(value['root']);relative(value['config']);text(value['architecture'])
    unique_strings(value['modalities']);unique_strings(value['weight_files'])
    if not isinstance(value['files'], dict) or not value['files']:raise ValueError('文件清单不能为空')
    for name, digest in value['files'].items():
        relative(name)
        if not isinstance(digest, str) or not HASH.fullmatch(digest):raise ValueError('资产 SHA256 无效')
    if set(value['weight_files'] + [value['config']]) - value['files'].keys():raise ValueError('config/权重未固定身份')
    if any(not name.endswith('.safetensors') for name in value['weight_files']):raise ValueError('本入口仅检查 safetensors')


def source(value):
    fields(value, ['baseline', 'working', 'revision', 'origin', 'allow'])
    absolute(value['baseline']);absolute(value['working']);text(value['revision']);reference(value['origin'])
    unique_strings(value['allow'])
    for name in value['allow']:relative(name)
    a, b = Path(value['baseline']).resolve(), Path(value['working']).resolve()
    if a.is_relative_to(b) or b.is_relative_to(a):raise ValueError('基线与工作副本必须隔离')


def capture_request(value):
    fields(value, ['schema_version', 'model', 'target', 'precheck', 'sources', 'references', 'required_checks'])
    model(value['model']);reference(value['precheck'])
    fields(value['target'], ['vllm', 'vllm_ascend'])
    for version in value['target'].values():text(version)
    if not isinstance(value['sources'], dict) or not value['sources']:raise ValueError('源码对象不能为空')
    for name, item in value['sources'].items():
        if not ID.fullmatch(name):raise ValueError('源码标识无效')
        source(item)
    if not isinstance(value['references'], list) or not value['references']:raise ValueError('参考资料不能为空')
    for item in value['references']:reference(item)
    unique_strings(value['required_checks'])


def validate(value, action):
    if action == 'capture':capture_request(value)
    else:
        fields(value, ['schema_version', 'context', 'weight_mapping', 'checks'])
        reference(value['context']);reference(value['weight_mapping'])
        if not isinstance(value['checks'], list):raise ValueError('checks 须为列表')
        for item in value['checks']:reference(item)
    if value['schema_version'] != '1.0':raise ValueError('仅支持 schema_version=1.0')
    return value


def installed_root():
    return release_root()


def roots(request, action):
    if action != 'capture':return []
    result = [Path(request['model']['root'])]
    for item in request['sources'].values():result.extend([Path(item['baseline']), Path(item['working'])])
    # 多来源之间不允许相互嵌套；输入绝不允许覆盖整个安装副本。
    installed = installed_root()
    for index, root in enumerate(result):
        if root.resolve() != root:raise ValueError('来源根不能经符号链接重定向')
        for other in [installed, *result[:index]]:
            if root.is_relative_to(other) or other.is_relative_to(root):raise ValueError('来源根互相重叠或侵入安装副本')
    return result


def output(work_root, run_id, protected):
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):raise ValueError('run-id 无效')
    root = Path(work_root).expanduser().absolute()
    target = root / run_id / 'adapt-agent'
    if target.resolve() != target:raise ValueError('输出不能经符号链接重定向')
    installed = installed_root()
    for path in [installed, *protected]:
        path = Path(path).resolve()
        if target.is_relative_to(path) or path.is_relative_to(target):raise ValueError('输出须在安装副本与所有来源之外')
    return target
