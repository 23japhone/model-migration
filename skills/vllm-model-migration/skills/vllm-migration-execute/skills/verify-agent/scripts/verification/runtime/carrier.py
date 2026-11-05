"""服务准备、readiness和精确交接的运行方式组装。"""
import json
import http.client
import re
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit

from . import docker, process
from .inspection import available, listener_inodes, running_evidence, runtime_identity, confirm_workers
from .registration import check
from .execution import prefix as target_prefix
from ..evidence import write_json
from ..protocol.http import exchange
from service_address import assert_local
from ..devices import mapping, allocation

ADAPTERS = {'docker': docker, 'process': process}


def prepare(request, attempt):
    service = request['service']
    assert_local(urlsplit(service['endpoint']).hostname)
    kind = request['runtime']['kind']
    mapping.execution_host(kind, attempt)
    nonce = uuid.uuid4().hex
    if service['ownership'] == 'borrowed':
        resource = {'kind': kind, 'ownership': 'borrowed', 'nonce': nonce, 'identity': service['identity']}
        if 'log_path' in service:resource['log_path'] = service['log_path']
        validate(resource, attempt)
    else:
        if listener_inodes(urlsplit(service['endpoint']).port):raise ValueError('端口已有监听，不能删除或复用无关服务')
        available(request, attempt)
        resource = ADAPTERS[kind].create(request, attempt, nonce)
    write_json(attempt / 'resource.json', resource)
    return resource


def validate(resource, attempt):
    if resource['kind'] == 'docker':
        observed = docker.inspect(resource['identity']['container_id'], attempt, 'current-container')
        docker.assert_identity(resource['identity'], observed)
        if not observed['running']:raise ValueError('容器未运行')
    elif resource.get('identity'):
        process.assert_identity(resource['identity'])


def inspect_runtime(request, resource, attempt):
    prefix = target_prefix(request, resource)
    return runtime_identity(request, resource, prefix, attempt)


def resolve_devices(request, resource, attempt):
    return mapping.prepare(request, resource, attempt)


def check_registration(request, resource, attempt):
    prefix = target_prefix(request, resource)
    return check(request, resource, prefix, attempt)


def wait_ready(request, resource, attempt):
    deadline = time.monotonic() + request['service']['timeout_seconds']
    polls = attempt / 'readiness'
    polls.mkdir()
    number = 0
    while time.monotonic() < deadline:
        validate(resource, attempt)
        folder = polls / str(number);folder.mkdir();number += 1
        try:
            models, _ = exchange(request['service']['endpoint'], '/v1/models', None, folder,
                                 min(5, max(1, deadline - time.monotonic())))
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
            write_json(folder / 'error.json', {'message': str(error)})
        else:
            model_card(models, request)
            return models
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError('服务就绪超时，未满足真实模型身份')


def model_card(models, request):
    cards = models.get('data')
    if not isinstance(cards, list):raise ValueError('实际models回执无效')
    matches = [v for v in cards if isinstance(v, dict) and v.get('id') == request['model']['name']]
    if len(matches) != 1:raise ValueError('实际served model identity不一致')
    root = matches[0].get('root')
    if not isinstance(root, str) or Path(root) != Path(request['model']['runtime_root']):
        raise ValueError('实际加载模型root与固定资产根不一致')


def launch(request, resource, attempt):
    if resource['ownership'] == 'owned':
        allocation.before_start(request, attempt, 'before-launch')
        ADAPTERS[resource['kind']].launch(request, resource, attempt)
    return wait_ready(request, resource, attempt)


def capture(request, resource, attempt):
    validate(resource, attempt)
    logs = service_logs(resource, attempt)
    (attempt / 'worker.log').write_text(logs)
    markers = request['service'].get('weight_markers', [])
    if not markers or not all(marker in logs for marker in markers):
        raise ValueError('缺少本次明确要求的真实权重加载日志证据')
    if re.search(r"load[_-]format\s*(?:=|:|\s)\s*['\"]?dummy", logs):
        raise ValueError('dummy load 不能作为真实权重验收')
    workers = running_evidence(request, resource, attempt)
    devices = mapping.capture(request, resource, attempt, workers)
    validate(resource, attempt)
    confirm_workers(workers, resource)
    result = {'resource': resource, 'endpoint': request['service']['endpoint'], 'model': request['model'],
              'runtime': request['runtime'], 'workers': workers, 'retention': request['service']['retention'],
              **devices}
    write_json(attempt / 'deployment-handoff.json', result)
    return result


def worker_signature(handoff):
    workers = handoff['workers']
    npu = sorted((v['device'], v['pid'], v['identity']['start_ticks']) for v in workers['npu_processes'])
    return workers['listener_identities'], npu


def final_handoff(request, resource, attempt, previous):
    current = capture(request, resource, attempt)
    if worker_signature(previous) != worker_signature(current):
        raise ValueError('请求期间API或NPU worker的PID/启动身份发生变化')
    return current


def service_logs(resource, attempt):
    if resource['kind'] == 'docker':return docker.logs(resource, attempt)
    return Path(resource['log_path']).read_text(errors='replace')


def failure_logs(resource, attempt):
    (attempt / 'failure-service.log').write_text(service_logs(resource, attempt))


def recover(attempt):
    pending = attempt / 'resource-pending.json'
    if not pending.exists():return docker.recover_intent(attempt)
    resource = json.loads(pending.read_text())
    if resource['kind'] == 'docker' and resource['identity']['started_at'] is None:
        actual = docker.inspect(resource['identity']['container_id'], attempt, 'recover-created')
        if (actual['image_id'] != resource['identity']['image_id']
                or actual['labels'].get('vllm.verify.owner') != resource['nonce']):
            raise ValueError('创建后身份不一致，不回收未知容器')
        resource['identity']['started_at'] = actual['started_at']
    return resource


def stop(resource, attempt):
    if resource['ownership'] != 'owned':raise ValueError('不得停止 borrowed 服务')
    return ADAPTERS[resource['kind']].stop(resource, attempt)
