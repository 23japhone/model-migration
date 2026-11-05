"""全量 NPU 候选池来自公开观察及 Agent 完整清单，不把卡数当成 TP。"""
from coordination.runtime import sources
from coordination.request import fields, reference
from .. import effects
from ..contract import integer, sequence
from .policy import unspecified
from .state import bind


def inventory(observations):
    value = observations['device_inventory']
    fields(value, ('source', 'complete', 'ids'))
    reference(value['source'])
    report = sources.bound(value['source'])
    if value['complete'] is not True or report.get('status') != 'completed' or report.get('operation') != 'observe':
        raise ValueError('device_inventory 必须绑定已完成公开 observe 的完整 NPU 清单')
    result = report['result']
    if not result.get('npu_raw') or not result.get('docker_id') or not result.get('docker_name'):
        raise ValueError('设备观察缺少实际 NPU 原文或主机身份')
    ids = sequence(value['ids'], 'device_inventory.ids', lambda item, name: integer(item, name, 0))
    observed = sequence([row['id'] for row in observations['deployment']['devices']], 'deployment.devices',
                        lambda item, name: integer(item, name, 0))
    if set(observed) != set(ids):
        raise ValueError('完整 NPU 清单与实际容量观察未覆盖相同设备')
    return sorted(ids), {key: result[key] for key in ('docker_id', 'docker_name')}, value['source']


def resolve(session, task, observations, work_root):
    if not unspecified(task['environment']['available_npu_devices']):
        return True
    if 'device_inventory' not in observations:
        effects.operation(session, 'default_device_observation', 'verify-agent',
                          {'operation': 'observe', 'host': '127.0.0.1', 'port': 8000}, work_root)
        raise ValueError('Agent 须根据公开 NPU 观察补齐 observations.device_inventory 的完整清单及容量观察')
    ids, identity, source = inventory(observations)
    bind(session, task, 'devices', {'values': {'environment.available_npu_devices': ids},
         'origin': 'host_inventory', 'identity': identity, 'evidence': [source]})
    return True


def check_host(session, observed):
    binding = session.value.get('default_resolutions', {}).get('devices')
    if binding and any(observed.get(key) != value for key, value in binding['identity'].items()):
        raise ValueError('部署实际主机与默认设备池的冻结主机身份不一致')
