"""合成的部署交接与本机 HTTP 替身；不调用设备、评测器或真实 msProbe。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = Path(os.environ.get('MSPROBE_TEST_BUNDLE', ROOT / 'skills/vllm-model-migration'))
AGENT = Path('skills/vllm-migration-execute/skills/verify-agent')


def reference(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return reference(path)


def isolated_bundle(root):
    """仅替代目标 Linux 身份观察；部署交接与全部采集解析保留生产实现。"""
    bundle = root / 'bundle'
    shutil.copytree(BUNDLE, bundle)
    scripts = bundle / AGENT / 'scripts'
    service = scripts / 'verification/runtime/capture/service.py'
    with service.open('a') as stream:
        stream.write('''
# TEST ONLY: no Linux process/device exists in this offline fixture.
def live(handoff, preparation, attempt):
    from verification.evidence import write_json
    write_json(attempt / 'current-container.command.json', ['fixture-inspect'])
    read_bound(preparation['config'])
    root = Path(preparation['config']['path']).parent
    for role in ('worker', 'engine', 'frontend'):
        record = state.read(root / (role + '.json'))
        if record['adapter_files'] != preparation['adapter_files']:
            raise ValueError('fixture adapter identity changed')
    return state.read(root / 'worker.json')
''')
    # A use of an evaluator SDK fails explicitly, even if installed on this host.
    (scripts / 'sitecustomize.py').write_text('''
import sys
class RejectEvaluator:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'evalscope', 'ais_bench', 'aisbench'}:
            raise RuntimeError('offline capture must not import evaluator SDK: ' + fullname)
sys.meta_path.insert(0, RejectEvaluator())
''')
    return scripts / 'verify.py'


def device_handoff(root, endpoint):
    declared = {'runtime': {'kind': 'process', 'env': {}}, 'model': {'name': 'synthetic-model'},
                'service': {'endpoint': endpoint, 'ownership': 'owned'}, 'npu': {'devices': [0]}}
    request = write(root / 'declared.json', declared)
    resource = {'kind': 'process', 'ownership': 'owned', 'nonce': 'synthetic-owner',
                'identity': {'pid': os.getpid(), 'start_ticks': 'synthetic'}}
    devices = [{'physical_id': 0, 'logical_id': 0, 'visible_index': 0}]
    probe = write(root / 'device-probe.json', {'status': 'completed', 'count': 1, 'devices': devices,
                  'library': 'synthetic-cann', 'executable_realpath': sys.executable, 'environment': {}})
    mapping = write(root / 'device-mapping.json', {
        'schema_version': '1.0', 'format': 'ascend-device-mapping-v1', 'request': request,
        'resource': resource, 'host': {'hostname': 'offline-fixture', 'boot_id': 'synthetic'},
        'selected_physical_devices': [0], 'devices': devices, 'base_probe': probe, 'visible_probe': probe,
        'library': 'synthetic-cann', 'executable_realpath': sys.executable})
    effective = write(root / 'effective-runtime.json', {'schema_version': '1.0',
        'format': 'ascend-effective-runtime-v1', 'request': request, 'device_mapping': mapping,
        'runtime': declared['runtime']})
    return declared, {'declared_request': request, 'device_mapping': mapping, 'effective_runtime': effective,
        'resource': resource, 'runtime': declared['runtime'], 'model': declared['model'], 'endpoint': endpoint,
        'final_service': {'state': 'retained'}}


def deployment(root, endpoint, cache=False):
    runtime = root / 'runtime'
    config = write(runtime / 'msprobe.json', {'task': 'tensor', 'level': 'mix', 'risk_level': 'ALL',
        'dump_enable': False, 'dump_path': str(runtime / 'dump'), 'tensor': {'data_mode': ['forward']}})
    extra = {}
    if cache:
        extra['decode_cache'] = write(runtime / 'decode-cache.json', {'status': 'available', 'version': '0.1.4',
                                                                      'max_snapshot_bytes': 65536})
    prep = write(root / 'attempt' / 'worker-capture-preparation.json', {**extra, 'run_id': 'fixture',
        'attempt_id': 'attempt', 'status': 'prepared', 'config': config, 'adapter_files': {'fixture': 'synthetic'},
        'dump': {'path': str(runtime / 'dump'), 'runtime_path': str(runtime / 'dump')}})
    ready = {'rank': 0, 'vocab_size': 100, 'layers': ['model.layers.0', 'model.layers.1'],
             'adapter_files': {'fixture': 'synthetic'}, 'coverage_capability': 'modules_and_registered_apis'}
    for role in ('worker', 'engine', 'frontend'):
        record = dict(ready)
        if cache and role in ('engine', 'worker'):
            record['decode_cache'] = {'status': 'ready', 'config_sha256': extra['decode_cache']['sha256']}
        write(runtime / (role + '.json'), record)
    declared, handoff = device_handoff(root, endpoint)
    capture = {'status': 'prepared', 'receipt': prep}
    bound_handoff = write(root / 'attempt' / 'deployment-handoff.json', {**handoff, 'worker_capture': capture})
    report = write(root / 'attempt' / 'verification-report.json', {
        'schema_version': '1.0', 'task_id': 'verify-agent', 'operation': 'verify', 'status': 'completed',
        'run_id': 'fixture', 'inputs': declared,
        'conclusions': dict.fromkeys(('workers', 'lifecycle', 'functional_protocol'), 'pass'),
        'evidence': {'handoff': bound_handoff['path']},
        'detailed_report': {'handoff_sha256': bound_handoff['sha256'], 'worker_capture': capture}})
    return report, runtime


def public(verify, root, request, action='capture-session'):
    path = root / 'cli-request.json'
    write(path, request)
    result = subprocess.run([sys.executable, '-B', str(verify), '--action', action, '--request', str(path),
                             '--work-root', str(root / 'out'), '--run-id', 'closure'],
                            text=True, capture_output=True, timeout=20,
                            env={**os.environ, 'PYTHONPATH': str(verify.parent)})
    value = json.loads(result.stdout) if result.stdout.strip() else {}
    return result, value
