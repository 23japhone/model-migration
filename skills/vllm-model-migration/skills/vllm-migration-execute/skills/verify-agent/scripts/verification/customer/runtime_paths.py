"""从确切 API 进程读取白名单环境及其挂载命名空间中的路径证据。"""
import json
from pathlib import Path

from ..evidence import sha256, write_json
from ..runtime import docker


def bound(reference):
    path = Path(reference['path'])
    if sha256(path) != reference['sha256']:
        raise ValueError('部署交接摘要不一致')
    return json.loads(path.read_text())


def identity(pid):
    fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'start_ticks': fields[19], 'pgid': int(fields[2])}


def capture(request, attempt):
    handoff = bound(request['deployment'])
    resource = handoff['resource']
    if resource['kind'] == 'docker':
        actual = docker.inspect(resource['identity']['container_id'], attempt, 'paths-container')
        docker.assert_identity(resource['identity'], actual)
    listeners = handoff['workers']['listener_identities']
    if len(listeners) != 1:
        raise ValueError('路径证据需要唯一 API 监听进程')
    expected = listeners[0]
    pid = expected['pid']
    if identity(pid) != expected:
        raise ValueError('API 进程身份变化')
    process = Path('/proc', str(pid))
    raw = dict(item.split('=', 1) for item in (process / 'environ').read_text().split('\0') if '=' in item)
    env = {key: raw[key] for key in ('ASCEND_HOME_PATH', 'ASCEND_TOOLKIT_HOME') if key in raw}
    argv = (process / 'cmdline').read_bytes().decode().strip('\0').split('\0')
    paths = list(env.values()) + [handoff['model']['runtime_root']]
    checked = {path: {'is_dir': (process / 'root' / path.lstrip('/')).is_dir()} for path in paths}
    if identity(pid) != expected:
        raise ValueError('API 进程在采集期间变化')
    records = [{'identity': expected, 'argv': argv, 'environment': env}]
    record_path = attempt / 'api-environment.json'
    write_json(record_path, records)
    result = {'schema_version': '1.0', 'deployment': request['deployment'],
              'processes': {'path': str(record_path), 'sha256': sha256(record_path)},
              'path_checks': {'identity': expected, 'paths': checked}}
    write_json(attempt / 'runtime-environment.json', result)
    return {'path': str(attempt / 'runtime-environment.json'),
            'sha256': sha256(attempt / 'runtime-environment.json')}
