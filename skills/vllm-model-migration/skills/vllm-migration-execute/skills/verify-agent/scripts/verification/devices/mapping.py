"""最终载体的两阶段探测、派生环境和不可变证据。"""
import copy
import json
import os
from pathlib import Path
import socket
import uuid
from urllib.parse import urlsplit

from device_contract import RT, VISIBLE, SELECTORS, ref, require, resource_binding
from .policy import resolve, validate_visible, creation_environment
from .workers import environment
from ..evidence import read_bound, write_json
from ..runtime.execution import prefix, execute


def host_identity():
    return {'hostname': socket.gethostname(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def execution_host(kind, attempt):
    host = host_identity()
    if kind == 'docker':
        from ..customer.inspection import local_daemon
        local_daemon(attempt)
    return host


def probe(request, resource, attempt, mode):
    runtime = request['runtime']
    payload = {'mode': mode, 'environment': {k: v for k, v in runtime['env'].items() if k in SELECTORS}}
    code = Path(__file__).with_name('probe.py').read_text()
    raw = execute(prefix(request, resource), ['-B', '-c', code], attempt / mode, timeout=60,
                  input_text=json.dumps(payload), env={**os.environ, **runtime['env']},
                  cwd=runtime.get('cwd') if resource['kind'] == 'process' else None)
    lines = [line[len('VLLM_DEVICE_JSON='):] for line in raw.splitlines() if line.startswith('VLLM_DEVICE_JSON=')]
    require(len(lines) == 1, '设备探针没有唯一有效回执')
    result = json.loads(lines[0])
    write_json(attempt / (mode + '-probe.json'), result)
    require(result.get('status') == 'completed', 'CANN 设备映射探测失败：' + str(result.get('diagnostic', result.get('finalize_error'))))
    return result, ref(attempt / (mode + '-probe.json'))


def borrowed_environment(request, resource):
    from ..runtime.inspection import listener_owners
    from ..runtime.process import identity
    pids = listener_owners(urlsplit(request['service']['endpoint']).port, resource)
    return environment(pids, request['runtime'], identity)


def prepare(request, resource, attempt):
    declared = {key: copy.deepcopy(value) for key, value in request.items() if not key.startswith('_')}
    path = attempt / 'declared-request.json'
    write_json(path, declared)
    target = attempt / 'devices-resolution'
    target.mkdir()
    execution = copy.deepcopy(request)
    if resource['ownership'] == 'borrowed':
        execution['runtime']['env'] = borrowed_environment(request, resource)
    elif resource['kind'] == 'docker':
        execution['runtime']['env'] = creation_environment(request)
    host = host_identity()
    base, base_ref = probe(execution, resource, target, 'base')
    env, selected = resolve(request['npu']['devices'], base['devices'], execution['runtime']['env'], resource['ownership'])
    execution['runtime']['env'] = env
    visible, visible_ref = probe(execution, resource, target, 'visible')
    validate_visible(selected, visible['devices'])
    require(base['library'] == visible['library'], '映射探测期间 CANN 库发生变化')
    require(host_identity() == host, '映射探测期间宿主启动身份变化')
    execution['_device_state'] = {'request': ref(path), 'host': host, 'resource': resource_binding(resource),
                                  'devices': selected, 'base_probe': base_ref,
                                  'visible_probe': visible_ref, 'library': visible['library'],
                                  'executable_realpath': visible['executable_realpath']}
    return execution


def assert_resource(expected, actual):
    # owned process 在创建载体阶段尚未启动；只允许首次绑定此前为空的身份。
    if expected['kind'] == 'process' and expected['ownership'] == 'owned' and expected['identity'] is None:
        expected = {**expected, 'identity': actual['identity']}
    require(expected == actual, '设备映射载体或启动身份变化')


def capture(request, resource, attempt, workers):
    state = request['_device_state']
    require(state['host'] == host_identity(), '宿主启动身份变化，旧设备映射失效')
    assert_resource(state['resource'], resource_binding(resource))
    from ..runtime.process import identity
    observed = environment(workers['listener_pids'], request['runtime'], identity)
    require(observed == request['runtime']['env'], '实际服务启动环境变化，旧设备映射失效')
    target = attempt / ('devices-' + uuid.uuid4().hex)
    target.mkdir()
    current, visible_ref = probe(request, resource, target, 'visible')
    validate_visible(state['devices'], current['devices'])
    require(current['library'] == state['library'], 'CANN 库身份变化，旧映射失效')
    require(current['executable_realpath'] == state['executable_realpath'], '映射探针解释器变化')
    mapping = {**state, 'schema_version': '1.0', 'format': 'ascend-device-mapping-v1',
               'resource': resource_binding(resource), 'selected_physical_devices': request['npu']['devices'],
               'visible_probe': visible_ref}
    write_json(target / 'device-mapping.json', mapping)
    mapping_ref = ref(target / 'device-mapping.json')
    effective = {'schema_version': '1.0', 'format': 'ascend-effective-runtime-v1',
                 'request': state['request'], 'device_mapping': mapping_ref, 'runtime': request['runtime']}
    write_json(target / 'effective-runtime.json', effective)
    return {'declared_request': state['request'], 'device_mapping': mapping_ref,
            'effective_runtime': ref(target / 'effective-runtime.json'),
            'runtime': read_bound(state['request'])['runtime']}
