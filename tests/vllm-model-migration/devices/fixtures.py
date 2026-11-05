"""设备证据夹具；所有硬件身份均为合成值，不代表实机结果。"""
import copy
import json
from pathlib import Path

from device_contract import ref, RT, VISIBLE, resource_binding


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return ref(path)


def attach(root, declared, handoff):
    root = Path(root)
    devices = [{'physical_id': n, 'logical_id': i, 'visible_index': i}
               for i, n in enumerate(declared['npu']['devices'])]
    env = {**declared['runtime']['env'], RT: ','.join(str(r['logical_id']) for r in devices),
           VISIBLE: ','.join(str(r['physical_id']) for r in devices)}
    original = write(root / 'request.json', declared)
    library = {'path': '/cann/lib64/libascendcl.so', 'sha256': 'c' * 64, 'api_version': [9, 0, 0]}
    probe = {'status': 'completed', 'devices': devices, 'count': len(devices),
             'library': library, 'executable_realpath': '/python', 'calls': [{'api': 'aclFinalize', 'return_code': 0}],
             'environment': {k: env[k] for k in (RT, VISIBLE)}}
    base = write(root / 'base.json', {**probe, 'environment': {VISIBLE: env[VISIBLE]}})
    visible = write(root / 'visible.json', probe)
    mapping = {'schema_version': '1.0', 'format': 'ascend-device-mapping-v1', 'request': original,
               'resource': resource_binding(handoff['resource']), 'host': {'hostname': 'synthetic', 'boot_id': 'boot'},
               'devices': devices, 'selected_physical_devices': declared['npu']['devices'],
               'base_probe': base, 'visible_probe': visible, 'library': library, 'executable_realpath': '/python'}
    mapping_ref = write(root / 'device-mapping.json', mapping)
    runtime = copy.deepcopy(declared['runtime']); runtime['env'] = env
    effective = write(root / 'effective-runtime.json', {'schema_version': '1.0',
        'format': 'ascend-effective-runtime-v1', 'request': original, 'device_mapping': mapping_ref, 'runtime': runtime})
    handoff.update(declared_request=original, device_mapping=mapping_ref, effective_runtime=effective)
    return runtime
