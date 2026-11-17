"""Docker 构建、容器身份和仅对本次归属资源的生命周期操作。"""
import json
import os
from pathlib import Path
import uuid

from .command import run
from .capture.launch import command as launch_command
from ..evidence import write_json
from ..devices.policy import creation_environment
from .execution import docker_prefix


def inspect(container, attempt, phase):
    value = json.loads(run(['docker', 'inspect', container], attempt / phase))[0]
    return {'container_id': value['Id'], 'image_id': value['Image'],
            'started_at': value['State']['StartedAt'], 'pid': value['State']['Pid'],
            'running': value['State']['Running'], 'labels': value['Config'].get('Labels') or {},
            'network': value['HostConfig']['NetworkMode'], 'raw': value}


def assert_identity(expected, actual):
    if any(actual.get(k) != expected[k] for k in ('container_id', 'image_id', 'started_at')):
        raise ValueError('容器精确身份或启动身份已变化')
    if actual['network'] != 'host':raise ValueError('本轮端口归属验证要求 host network')


def build(request, attempt):
    item = request['build']
    iid = attempt / 'build-image.id'
    env = dict(os.environ)
    if item.get('builder') == 'legacy':env['DOCKER_BUILDKIT'] = '0'
    write_json(attempt / 'build-environment.json', {'builder': item.get('builder', 'default'),
                                                'DOCKER_BUILDKIT': env.get('DOCKER_BUILDKIT')})
    run(['docker', 'build', '--iidfile', str(iid), '-f', str(Path(item['context']) / item['dockerfile']),
         item['context']], attempt / 'image-build', timeout=3600, env=env)
    image_id = iid.read_text().strip()
    if not image_id.startswith('sha256:') or len(image_id) != 71:raise ValueError('构建未产生实际 image ID')
    return image_id


def create(request, attempt, nonce):
    config = request['service']['docker']
    image = build(request, attempt) if 'build' in request else config['image_id']
    argv = ['docker', 'create', '--name', config['name'], '--network', 'host',
            '--label', 'vllm.verify.owner=' + nonce, '--shm-size', str(config['shm_size']),
            '--memory', str(config['memory']), '--cpus', str(config['cpus'])]
    for device in config['devices']:argv += ['--device', device]
    for mount in config['mounts']:
        value = 'type=bind,src=' + mount['source'] + ',dst=' + mount['target']
        argv += ['--mount', value + (',readonly' if mount['read_only'] else '')]
    for key, value in creation_environment(request).items():argv += ['--env', key + '=' + value]
    if request['runtime'].get('cwd'):argv += ['--workdir', request['runtime']['cwd']]
    argv += ['--entrypoint', request['runtime']['python'], image, '-c', 'import time; time.sleep(604800)']
    write_json(attempt / 'creation-intent.json', {'nonce': nonce, 'image_id': image, 'name': config['name']})
    container = run(argv, attempt / 'container-create').strip()
    # 后续外部操作前保存CID，启动失败时仍能按精确身份恢复清理。
    resource = {'kind': 'docker', 'ownership': 'owned', 'nonce': nonce,
                'identity': {'container_id': container, 'image_id': image, 'started_at': None}}
    write_json(attempt / 'resource-pending.json', resource)
    run(['docker', 'start', container], attempt / 'container-start')
    observed = inspect(container, attempt, 'container-created')
    resource['identity'] = {k: observed[k] for k in ('container_id', 'image_id', 'started_at')}
    return resource


def launch(request, resource, attempt):
    container = resource['identity']['container_id']
    path = '/tmp/vllm-verify-' + resource['nonce'] + '.log'
    resource['log_path'] = path
    code = 'import subprocess,sys; f=open(sys.argv[1], "ab", buffering=0); raise SystemExit(subprocess.call(sys.argv[2:], stdout=f, stderr=f))'
    run([*docker_prefix(resource, request['runtime']['env'], ('-d',)), request['runtime']['python'], '-c', code,
         path, *launch_command(request)], attempt / 'service-launch')
    write_json(attempt / 'resource-pending.json', resource)


def logs(resource, attempt):
    if resource.get('log_path'):
        return run(['docker', 'exec', resource['identity']['container_id'], 'cat', resource['log_path']],
                   attempt / 'service-logs')
    output = run(['docker', 'logs', resource['identity']['container_id']], attempt / 'service-logs')
    return output + (attempt / 'service-logs.stderr.log').read_text()


def stop(resource, attempt):
    attempt = attempt / 'stop-attempts' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    current = inspect(resource['identity']['container_id'], attempt, 'before-stop')
    assert_identity(resource['identity'], current)
    if current['labels'].get('vllm.verify.owner') != resource['nonce']:
        raise ValueError('容器 owner nonce 不一致，拒绝停止')
    run(['docker', 'stop', '--time', '10', resource['identity']['container_id']], attempt / 'stop', timeout=30)
    after = inspect(resource['identity']['container_id'], attempt, 'after-stop')
    if after['running']:raise RuntimeError('owned 容器仍在运行')
    return {'state': 'stopped', 'identity': resource['identity']}


def recover_intent(attempt):
    path = attempt / 'creation-intent.json'
    if not path.exists():return None
    intent = json.loads(path.read_text())
    output = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=vllm.verify.owner=' + intent['nonce']],
                 attempt / 'recover-create-query')
    ids = output.split()
    if not ids:return None
    if len(ids) != 1:raise ValueError('owner nonce 匹配多个资源，拒绝猜测')
    actual = inspect(ids[0], attempt, 'recover-create-inspect')
    if (actual['image_id'] != intent['image_id'] or actual['raw']['Name'] != '/' + intent['name']
            or actual['labels'].get('vllm.verify.owner') != intent['nonce']):
        raise ValueError('创建意图与实际容器身份不一致')
    return {'kind': 'docker', 'ownership': 'owned', 'nonce': intent['nonce'],
            'identity': {k: actual[k] for k in ('container_id', 'image_id', 'started_at')}}
