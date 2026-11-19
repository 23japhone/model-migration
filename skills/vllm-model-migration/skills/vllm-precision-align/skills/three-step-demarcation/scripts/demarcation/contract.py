"""定界请求形状与输出隔离边界；计划不解引用。"""
import sys as _sys
from pathlib import Path as _Path
_sys.dont_write_bytecode = True
_sys.path.insert(0, str(_Path(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root  # noqa: E402
import re
from pathlib import Path
from urllib.parse import urlsplit


def object_fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) \
            or set(required) - set(value):
        raise ValueError('请求对象缺少必需字段或含未知字段')


def reference(value):
    object_fields(value, ('path', 'sha256'))
    if not isinstance(value['path'], str) or not Path(value['path']).is_absolute() \
            or str(Path(value['path'])) != value['path']:
        raise ValueError('引用路径必须为规范绝对路径')
    if not isinstance(value['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['sha256']):
        raise ValueError('文件引用 SHA256 无效')


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.port is None:
        raise ValueError('服务地址无效，须为 http(s)://host:port')


def run_name(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('run-id 须为单层安全名称')


def validate(request, action):
    if not isinstance(request, dict) or request.get('schema_version') != '1.0':
        raise ValueError('不支持的请求版本')
    if action == 'analyze':
        object_fields(request, ('schema_version', 'source'))
        reference(request['source'])
        return request
    object_fields(request, ('schema_version', 'service', 'badcase'),
                  ('render_mode', 'max_tokens', 'temperature', 'timeout_seconds'))
    object_fields(request['service'], ('endpoint',), ('model',))
    endpoint(request['service']['endpoint'])
    if request['service'].get('model') is not None \
            and (not isinstance(request['service']['model'], str) or not request['service']['model']):
        raise ValueError('服务模型名无效')
    reference(request['badcase'])
    mode = request.get('render_mode', 'auto')
    if mode not in ('auto', 'render', 'tokenize'):
        raise ValueError('render_mode 仅支持 auto/render/tokenize(local 需 transformers, 不支持)')
    timeout = request.get('timeout_seconds', 60)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 86400:
        raise ValueError('超时须为1至86400秒')
    return request


def output_parent(work_root, run_id):
    run_name(run_id)
    root = Path(work_root).expanduser().absolute()
    target = root / run_id / 'three-step-demarcation'
    check_output(target)
    return target


def check_output(target_path):
    target_path = Path(target_path)
    if target_path.resolve() != target_path:
        raise ValueError('输出不得通过符号链接重定向')
    boundary = release_root()
    if target_path.is_relative_to(boundary) or boundary.is_relative_to(target_path):
        raise ValueError('输出必须位于完整技能包之外')
