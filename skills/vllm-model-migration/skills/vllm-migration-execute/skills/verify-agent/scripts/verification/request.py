"""显式请求形状与包外输出边界；计划模式不读目标资料。"""
import json
import base64
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from .inputs import HANDOFF_TASKS
from . import outputs
from .runtime.capture import contract as capture_contract

HASH = re.compile(r'[0-9a-f]{64}')
SECRET = re.compile(r'(?i)(?:^|[-_])(?:token|password|secret|api[_-]?key|authorization)(?:$|[=_-])')


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('请求字段缺失、未知或对象类型无效')


def absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('路径须为目标执行环境中的绝对路径')
    return value


def hashes(value, relative=False):
    if not isinstance(value, dict) or not value:
        raise ValueError('身份文件清单不能为空')
    for name, digest in value.items():
        if relative:
            if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('资产清单路径须在模型根内')
        else:
            absolute(name)
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError('SHA256 格式无效')


def argv(value):
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v or '\0' in v for v in value):
        raise ValueError('命令须为非空 argv 字符串数组，不执行 shell 拼接')
    if any(SECRET.search(v) for v in value):
        raise ValueError('命令不得携带凭据，使用现有授权环境')


def model_fields(model):
    fields(model, ['name', 'root', 'runtime_root', 'files', 'weight_files', 'config'])
    if not isinstance(model['name'], str) or not model['name'].strip():
        raise ValueError('模型服务名称不能为空')
    absolute(model['root']);absolute(model['runtime_root']);hashes(model['files'], relative=True)
    weights = model['weight_files']
    if (not isinstance(weights, list) or not weights or len(weights) != len(set(weights))
            or any(v not in model['files'] for v in weights) or model['config'] not in model['files']):
        raise ValueError('权重及原始配置须完整列入内容身份清单')


def runtime_fields(runtime):
    fields(runtime, ['kind', 'python', 'versions', 'files', 'env'], ['cwd'])
    if runtime['kind'] not in ('docker', 'process'):
        raise ValueError('运行方式须为 docker 或 Linux process')
    argv([runtime['python']]);hashes(runtime['files'])
    if not isinstance(runtime['versions'], dict) or not runtime['versions']:
        raise ValueError('必须固定实际安装包版本')
    if not isinstance(runtime['env'], dict) or any(SECRET.search(k) for k in runtime['env']):
        raise ValueError('环境配置不能包含凭据值')
    if any(not isinstance(v, str) for v in runtime['env'].values()):
        raise ValueError('环境配置值须为字符串')
    if 'cwd' in runtime:absolute(runtime['cwd'])


def service_fields(service, kind):
    fields(service, ['ownership', 'endpoint', 'retention', 'timeout_seconds', 'weight_markers'],
           ['identity', 'command', 'docker', 'log_path'])
    if not isinstance(service['weight_markers'], list) or not service['weight_markers'] or any(
            not isinstance(v, str) or not v.strip() for v in service['weight_markers']):
        raise ValueError('须显式声明真实权重加载日志标记')
    endpoint_fields(service['endpoint'])
    if service['ownership'] not in ('owned', 'borrowed') or service['retention'] not in ('keep', 'stop'):
        raise ValueError('资源归属或保留策略无效')
    if not isinstance(service['timeout_seconds'], (int, float)) or not 1 <= service['timeout_seconds'] <= 3600:
        raise ValueError('服务超时须为 1–3600 秒')
    if service['ownership'] == 'borrowed':
        borrowed_fields(service, kind)
    else:
        argv(service['command'])
        real_load(service['command'])
        if kind == 'docker':docker_fields(service['docker'])


def real_load(command):
    for i, value in enumerate(command):
        if value == '--load-format=dummy' or (value == '--load-format' and command[i + 1:i + 2] == ['dummy']):
            raise ValueError('dummy load 不是真实权重验收')


def endpoint_fields(endpoint):
    from service_address import endpoint as parse_endpoint
    parse_endpoint(endpoint)


def borrowed_fields(service, kind):
    if service['retention'] != 'keep':
        raise ValueError('borrowed 服务不得自动停止')
    identity = service['identity']
    if kind == 'docker':
        fields(identity, ['container_id', 'image_id', 'started_at'])
        if not HASH.fullmatch(identity['container_id']) or not re.fullmatch(r'sha256:[0-9a-f]{64}', identity['image_id']):
            raise ValueError('复用容器须固定完整 container/image ID')
    else:
        fields(identity, ['pid', 'start_ticks', 'pgid'])
        if not isinstance(identity['pid'], int) or identity['pid'] < 2:raise ValueError('PID 无效')
    if 'log_path' in service:absolute(service['log_path'])


def docker_fields(docker):
    fields(docker, ['image_id', 'name', 'devices', 'mounts', 'memory', 'shm_size', 'cpus'])
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', docker['image_id']):raise ValueError('镜像须固定实际 image ID')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', docker['name']):raise ValueError('容器名称无效')
    if not isinstance(docker['devices'], list) or not docker['devices']:raise ValueError('设备须显式指定')
    for path in docker['devices']:absolute(path)
    for item in docker['mounts']:
        fields(item, ['source', 'target', 'read_only'])
        absolute(item['source']);absolute(item['target'])
        if not isinstance(item['read_only'], bool) or ',' in item['source'] + item['target']:
            raise ValueError('挂载参数无效')


def case_fields(cases, model, required_features):
    if not isinstance(cases, list) or not cases:raise ValueError('必需功能用例不能为空')
    ids = set();covered = set();images = []
    for case in cases:
        case_shape(case, ids)
        ids.add(case['id'])
        if case['payload'].get('model') != model:raise ValueError('功能请求 model 身份不一致')
        images.extend(payload_fields(case))
        semantic_fields(case['semantic'])
        if bool(case['payload'].get('stream')) != (case['kind'] == 'stream'):
            raise ValueError('流式用例与请求不一致')
        if case['required']:covered.add(case['kind'])
    if len(images) != len(set(images)):
        raise ValueError('图像差异用例不能使用相同图像内容')
    if not required_features or set(required_features) - covered:raise ValueError('必需功能未有必需用例覆盖')
    if 'image' in required_features and sum(c['kind'] == 'image' and c['required'] for c in cases) < 2:
        raise ValueError('图像功能至少需要两个差异用例')


def case_shape(case, ids):
    fields(case, ['id', 'kind', 'route', 'payload', 'semantic', 'required'], ['min_prompt_tokens', 'expected_prompt_tokens'])
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', case['id']) or case['id'] in ids:
        raise ValueError('用例标识无效或重复')
    if case['kind'] not in ('text', 'chat', 'stream', 'image'):raise ValueError('本项只支持文本/chat/SSE/图像')
    if case['route'] not in ('/v1/completions', '/v1/chat/completions'):raise ValueError('功能请求路径无效')
    if not isinstance(case['required'], bool) or not isinstance(case['payload'], dict):
        raise ValueError('功能请求 model 身份与必需标志无效')
    minimum = case.get('min_prompt_tokens', 0)
    if type(minimum) is not int or minimum < 0:raise ValueError('min_prompt_tokens须为非负整数')
    if 'expected_prompt_tokens' in case:
        expected = case['expected_prompt_tokens']
        if type(expected) is not int or expected < 1:raise ValueError('expected_prompt_tokens须为正整数')


def payload_fields(case):
    payload = case['payload']
    if not isinstance(payload.get('stream', False), bool):raise ValueError('stream 须为布尔值')
    if payload.get('n', 1) != 1:raise ValueError('本次仅验证唯一完整生成')
    if case['kind'] == 'text':
        if case['route'] != '/v1/completions' or not isinstance(payload.get('prompt'), str) or not payload['prompt']:
            raise ValueError('text 用例须请求基础文本续写')
        return []
    if case['route'] != '/v1/chat/completions' or not isinstance(payload.get('messages'), list) or not payload['messages']:
        raise ValueError('chat/SSE/image 须提供真实消息请求')
    images = message_images(payload['messages'])
    if bool(images) != (case['kind'] == 'image'):
        raise ValueError('图像功能须有实际图像输入；其他用例不夹带图像')
    return images


def message_images(messages):
    images = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant'):
            raise ValueError('消息角色或结构无效')
        if isinstance(message.get('content'), list):
            for part in message['content']:
                if not isinstance(part, dict):raise ValueError('消息内容项结构无效')
                if part.get('type') == 'image_url':images.append(image_id(part))
    return images


def image_id(part):
    uri = part['image_url']['url']
    if not isinstance(uri, str) or not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+', uri):
        raise ValueError('验收图像须为身份固定的内嵌图片')
    raw = base64.b64decode(uri.split(',', 1)[1], validate=True)
    if not raw or len(raw) > 10 * 1024 * 1024:raise ValueError('图片为空或超出10MiB限制')
    return hashlib.sha256(raw).hexdigest()


def semantic_fields(value):
    fields(value, ['mode'], ['expected'])
    if value['mode'] not in ('exact', 'review'):raise ValueError('语义须为预先确定的 exact 或独立 review')
    if value['mode'] == 'exact' and (not isinstance(value.get('expected'), list) or not value['expected']
                                   or any(not isinstance(v, str) or not v.strip() for v in value['expected'])):
        raise ValueError('exact 须提供有效完整预期文本')


def load(path):
    request = json.loads(Path(path).read_text(encoding='utf-8'))
    fields(request, ['schema_version', 'model', 'runtime', 'service', 'registration', 'npu',
                     'required_features', 'cases'], ['build', 'handoffs', 'api_key_env', 'worker_capture'])
    if request['schema_version'] != '1.0':raise ValueError('仅支持 schema_version=1.0')
    model_fields(request['model']);runtime_fields(request['runtime'])
    service_fields(request['service'], request['runtime']['kind'])
    case_fields(request['cases'], request['model']['name'], request['required_features'])
    fields(request['registration'], ['resources', 'request'])
    for value in request['registration'].values():absolute(value)
    fields(request['npu'], ['devices'], ['allocation'])
    if 'allocation' in request['npu']:
        reference = request['npu']['allocation']
        fields(reference, ['path', 'sha256'])
        absolute(reference['path'])
        if not isinstance(reference['sha256'], str) or not HASH.fullmatch(reference['sha256']):
            raise ValueError('分配回执摘要无效')
    devices = request['npu']['devices']
    if (not isinstance(devices, list) or not devices or len(devices) != len(set(devices))
            or any(type(v) is not int or v < 0 for v in devices)):
        raise ValueError('必须显式选择唯一的物理 NPU 编号')
    execution_identity(request)
    optional_fields(request)
    capture_contract.validate(request)
    return request


def argument(command, flag):
    values = []
    for i, value in enumerate(command):
        if value == flag:
            if i + 1 >= len(command) or command[i + 1].startswith('--'):
                raise ValueError('启动参数缺少明确值')
            values.append(command[i + 1])
        elif value.startswith(flag + '='):values.append(value[len(flag) + 1:])
    if len(values) > 1:raise ValueError('启动参数重复，不能猜测最终身份')
    return values[0] if values else None


def execution_identity(request):
    if request['service']['ownership'] != 'owned':return
    service = request['service'];command = service['command']
    model = argument(command, '--model')
    if model is None and 'serve' in command:
        index = command.index('serve')
        model = command[index + 1] if index + 1 < len(command) else None
    if model is None or Path(model) != Path(request['model']['runtime_root']):
        raise ValueError('启动model路径与已固定运行资产根不一致')
    if argument(command, '--served-model-name') != request['model']['name']:
        raise ValueError('启动served model name与本次请求不一致')
    if (argument(command, '--port') != str(urlsplit(service['endpoint']).port)
            or not argument(command, '--host')):
        raise ValueError('启动端口须匹配，且host必须显式指定')
    if request['runtime']['kind'] == 'docker':docker_devices(request)


def docker_devices(request):
    devices = request['service']['docker']['devices']
    if any(':' in path for path in devices):raise ValueError('本次不接受设备节点重映射')
    ids = {int(match[1]) for path in devices if (match := re.fullmatch(r'/dev/davinci(\d+)', path))}
    if ids != set(request['npu']['devices']):
        raise ValueError('Docker计算节点与声明的物理NPU编号不一致')


def optional_fields(request):
    if 'build' in request:
        fields(request['build'], ['context', 'files', 'dockerfile'], ['builder'])
        if request['build'].get('builder', 'default') not in ('default', 'legacy'):
            raise ValueError('builder 须为 default 或显式 legacy')
        absolute(request['build']['context']);hashes(request['build']['files'], relative=True)
        if request['runtime']['kind'] != 'docker' or request['service']['ownership'] != 'owned':
            raise ValueError('仅新建 Docker 载体可以构建')

    if 'api_key_env' in request and not re.fullmatch('[A-Z][A-Z0-9_]{1,100}', request['api_key_env']):
        raise ValueError('api_key_env须为已有环境变量名，不接受凭据值')
    handoffs = request.get('handoffs', {})
    if not isinstance(handoffs, dict) or set(handoffs) - HANDOFF_TASKS.keys():
        raise ValueError('handoffs须为已定义任务的回执对象')
    for item in handoffs.values():
        fields(item, ['path', 'sha256'])
        absolute(item['path'])
        if not isinstance(item['sha256'], str) or not HASH.fullmatch(item['sha256']):
            raise ValueError('交接摘要无效')


def output_target(request, work_root, run_id):
    return outputs.target(work_root, run_id, outputs.protected_roots(request))
