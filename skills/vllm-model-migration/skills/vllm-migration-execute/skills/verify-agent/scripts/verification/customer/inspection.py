"""部署前观察、已退出同名容器自动替换及原显式授权路径；按精确CID删除。"""
import json
import os
from pathlib import Path
import platform
import re

from ..runtime.command import run
from ..runtime import docker
from . import npu_capacity
from service_address import access_host, free_port


def host(request, attempt):
    if platform.system() != 'Linux':
        raise ValueError('客户部署需在已授权 Linux/Ascend 主机执行，当前主机不符合条件')
    if request.get('capacity') is True:
        capacity = npu_capacity.observe(attempt)
        npu = capacity['npu_raw']
    else:
        capacity = {}
        npu = run(['npu-smi', 'info'], attempt / 'npu-observation', timeout=30)
    local_daemon(attempt)
    daemon = json.loads(run(['docker', 'info', '--format', '{{json .}}'], attempt / 'docker-info', timeout=30))
    if daemon.get('OSType') != 'linux':
        raise ValueError('Docker daemon 必须是本机 Linux')
    # 普通观察保留原文；容量模式只接受已支持的完整表格。
    port = free_port(access_host(request['host']), request['port'])
    return {'host': request['host'], 'port': port, 'architecture': platform.machine(),
            'cpu_affinity': sorted(os.sched_getaffinity(0)), 'meminfo': Path('/proc/meminfo').read_text(),
            'npu_raw': npu, 'docker_name': daemon.get('Name'), 'docker_id': daemon.get('ID'),
            'npu_source': str(attempt / 'npu-observation.stdout.log'),
            'container': existing_container(request.get('container_name'), attempt), **capacity}


def local_daemon(attempt):
    selected = os.environ.get('DOCKER_CONTEXT')
    command = ['docker', 'context', 'inspect'] + ([selected] if selected else [])
    contexts = json.loads(run(command, attempt / 'docker-context', timeout=30))
    endpoint = contexts[0]['Endpoints']['docker']['Host']
    if not selected:
        endpoint = os.environ.get('DOCKER_HOST') or endpoint
    if not endpoint.startswith('unix://'):
        raise ValueError('客户部署只支持本机 Unix socket Docker daemon')


def existing_container(name, attempt):
    if name is None:
        return None
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', name):
        raise ValueError('容器名称无效')
    ids = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'name=^/' + re.escape(name) + '$'],
              attempt / 'container-name-observation', timeout=30).split()
    if not ids:
        return None
    if len(ids) != 1:
        raise ValueError('同名容器查询不唯一')
    value = docker.inspect(ids[0], attempt, 'container-identity')
    identity = {key: value[key] for key in ('container_id', 'image_id', 'started_at')}
    return {**identity, 'status': value['raw']['State']['Status'], 'running': value['running']}


def removal_command(request, before):
    consent = request.get('consent')
    if consent is not None:
        if (not isinstance(consent, dict) or consent.get('approved') is not True
                or consent.get('identity') != request['identity']):
            raise ValueError('删除同名容器的显式授权须绑定完整容器身份')
        return ['docker', 'rm', '--force', request['identity']['container_id']]
    if before.get('running') is not False or before['raw']['State'].get('Status') != 'exited':
        raise ValueError('无额外授权的自动替换仅适用于再次核验仍已退出的容器')
    # 不带--force：inspect后若容器重新运行，由Docker拒绝删除，不重试强删。
    return ['docker', 'rm', request['identity']['container_id']]


def replace(request, attempt):
    expected = request['identity']
    if not isinstance(expected, dict) or set(expected) != {'container_id', 'image_id', 'started_at'}:
        raise ValueError('删除请求缺少完整容器身份')
    if not re.fullmatch('[a-f0-9]{64}', expected['container_id']):
        raise ValueError('删除请求必须使用完整容器 ID')
    local_daemon(attempt)
    before = docker.inspect(expected['container_id'], attempt, 'replacement-before')
    if any(before[key] != value for key, value in expected.items()):
        raise ValueError('同名容器身份已变化，原删除请求不再适用')
    if before['raw']['Name'].lstrip('/') != request['container_name']:
        raise ValueError('待删除容器名称与客户任务不一致')
    run(removal_command(request, before), attempt / 'replacement-remove', timeout=60)
    remaining = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + expected['container_id']],
                    attempt / 'replacement-after', timeout=30).strip()
    if remaining:
        raise ValueError('容器删除后仍可观察到原身份')
    return {'removed': expected, 'container_name': request['container_name']}
