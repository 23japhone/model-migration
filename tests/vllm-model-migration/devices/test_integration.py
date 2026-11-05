"""最终载体两阶段探测、原请求冻结和只读复核集成。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from device_contract import load, ref, RT, VISIBLE, bound
from devices.fixtures import write
from verification.devices import mapping, recheck
from verification import runner


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.request = {'runtime': {'kind': 'docker', 'python': '/python', 'env': {}, 'versions': {}, 'files': {}},
                        'npu': {'devices': [4, 7]}, 'model': {'root': '/model'},
                        'service': {'endpoint': 'http://127.0.0.1:8000', 'ownership': 'owned', 'retention': 'keep'}}
        self.resource = {'kind': 'docker', 'ownership': 'owned', 'nonce': 'owner',
                         'identity': {'container_id': 'a'*64, 'image_id': 'sha256:'+'b'*64, 'started_at': 'start'}}
        self.library = {'path': '/cann/libascendcl.so', 'sha256': 'c'*64, 'api_version': [9, 0, 0]}
        self.calls = []
        host = patch.object(mapping, 'host_identity', return_value={'hostname': 'synthetic', 'boot_id': 'boot'})
        host.start(); self.addCleanup(host.stop)
        execute = patch.object(mapping, 'execute', side_effect=self.execute)
        execute.start(); self.addCleanup(execute.stop)
        daemon = patch('verification.customer.inspection.local_daemon')
        daemon.start(); self.addCleanup(daemon.stop)

    def execute(self, prefix, argv, log, **kwargs):
        payload = json.loads(kwargs['input_text'])
        self.calls.append((payload, prefix))
        logical = [1, 0]
        if payload['mode'] == 'visible':
            logical = list(map(int, payload['environment'].get(RT, '1,0').split(',')))
        devices = [{'physical_id': {1: 4, 0: 7}[n], 'logical_id': n, 'visible_index': i} for i, n in enumerate(logical)]
        env = dict(payload['environment'])
        if payload['mode'] == 'base':env.pop(RT, None)
        result = {'status': 'completed', 'count': 2, 'devices': devices, 'environment': env,
                  'library': self.library, 'executable_realpath': '/python',
                  'calls': [{'api': 'aclFinalize', 'return_code': 0}]}
        return 'VLLM_DEVICE_JSON=' + json.dumps(result)

    def prepared(self):
        request = mapping.prepare(self.request, self.resource, self.root)
        workers = {'listener_pids': [100], 'listener_identities': [{'pid': 100, 'pgid': 100, 'start_ticks': '1'}],
                   'npu_processes': [{'device': n, 'pid': 101+i, 'identity': {
                       'pid': 101+i, 'pgid': 100, 'start_ticks': str(i+2)}} for i, n in enumerate((4, 7))]}
        with patch.object(mapping, 'environment', return_value=request['runtime']['env']):
            evidence = mapping.capture(request, self.resource, self.root, workers)
        handoff = {'resource': self.resource, 'model': self.request['model'],
                   'endpoint': self.request['service']['endpoint'], 'workers': workers, **evidence}
        return request, handoff

    def test_two_probes_use_final_container_and_export_bound_mapping(self):
        original = copy.deepcopy(self.request)
        execution, handoff = self.prepared()
        self.assertEqual(self.request, original)
        self.assertEqual(execution['runtime']['env'][RT], '1,0')
        declared, mapping_value, runtime = load(handoff, original)
        self.assertEqual(mapping_value['devices'][0]['physical_id'], 4)
        self.assertEqual(runtime['env'][VISIBLE], '4,7')
        self.assertEqual(handoff['runtime'], original['runtime'])
        for _, prefix in self.calls:self.assertIn(self.resource['identity']['container_id'], prefix)
        self.assertIn(RT + '=1,0', self.calls[1][1])

    def test_unknown_cann_mapping_blocks_model_start_but_keeps_borrowed_service(self):
        request = copy.deepcopy(self.request); request['service']['ownership'] = 'borrowed'
        resource = {**self.resource, 'ownership': 'borrowed'}
        with patch.object(runner, 'collect', return_value={'errors': [], 'model': {'architectures': ['Model']}}), \
             patch.object(runner.carrier, 'prepare', return_value=resource), \
             patch.object(mapping, 'borrowed_environment', return_value={}), \
             patch.object(mapping, 'execute', return_value='VLLM_DEVICE_JSON=' + json.dumps(
                 {'status': 'blocked', 'diagnostic': {'type': 'AttributeError', 'message': 'missing symbol'}})), \
             patch.object(runner.carrier, 'validate'), patch.object(runner.carrier, 'launch') as launch, \
             patch.object(runner.carrier, 'stop') as stop, patch.object(runner.carrier, 'check_registration') as register:
            code, result = runner.run(request, self.root / 'runs', 'blocked')
        self.assertEqual(code, 3)
        launch.assert_not_called(); stop.assert_not_called(); register.assert_not_called()
        report = json.loads(Path(result['report_path']).read_text())
        self.assertEqual(report['detailed_report']['final_service']['state'], 'retained')
        self.assertIn('missing symbol', report['diagnostic']['message'])
        self.assertEqual(next(row for row in report['detailed_report']['ledger'] if row['step'] == 'registration')['status'], 'blocked')
        self.assertNotIn('_architectures', report['inputs'])

    def recheck(self, handoff, workers=None):
        source = write(self.root / 'handoff.json', handoff)
        with patch.object(recheck.carrier, 'validate'), \
             patch.object(recheck.inspection, 'running_evidence', return_value=workers or handoff['workers']), \
             patch.object(mapping, 'environment', return_value=bound(handoff['effective_runtime'])['runtime']['env']), \
             patch.object(recheck.inspection, 'confirm_workers'), \
             patch.object(recheck.carrier, 'stop') as stop, patch.object(recheck.carrier, 'launch') as launch:
            result = recheck.inspect({'handoff': source}, self.root / 'checks', 'recheck')
        stop.assert_not_called(); launch.assert_not_called()
        return result

    def test_readonly_recheck_passes_without_service_lifecycle_effects(self):
        _, handoff = self.prepared()
        code, result = self.recheck(handoff)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['conclusions'], {'devices': 'pass'})

    def test_recheck_detects_host_reboot_and_worker_pid_reuse(self):
        _, handoff = self.prepared()
        with patch.object(mapping, 'host_identity', return_value={'hostname': 'synthetic', 'boot_id': 'new'}):
            code, result = self.recheck(handoff)
        self.assertEqual(code, 3)
        workers = copy.deepcopy(handoff['workers'])
        workers['npu_processes'][0]['identity']['start_ticks'] = 'changed'
        code, result = self.recheck(handoff, workers)
        self.assertEqual(code, 3)
        self.assertFalse(result['success'])

    def test_mapping_change_between_start_and_capture_is_rejected(self):
        execution = mapping.prepare(self.request, self.resource, self.root)
        self.library = {**self.library, 'sha256': 'd'*64}
        with patch.object(mapping, 'environment', return_value=execution['runtime']['env']), self.assertRaisesRegex(ValueError, 'CANN'):
            mapping.capture(execution, self.resource, self.root, {'listener_pids': [100]})

    def test_borrowed_service_new_selector_invalidates_recorded_environment(self):
        self.request['service']['ownership'] = self.resource['ownership'] = 'borrowed'
        with patch.object(mapping, 'borrowed_environment', return_value={}):
            execution = mapping.prepare(self.request, self.resource, self.root)
        with patch.object(mapping, 'environment', return_value={RT: '1,0'}), \
             self.assertRaisesRegex(ValueError, '启动环境'):
            mapping.capture(execution, self.resource, self.root, {'listener_pids': [100]})

    def test_old_handoff_returns_blocked_receipt_without_probing(self):
        source = write(self.root / 'old.json', {'model': self.request['model'], 'runtime': self.request['runtime']})
        with patch.object(recheck.carrier, 'validate') as validate:
            code, result = recheck.inspect({'handoff': source}, self.root / 'checks', 'old')
        self.assertEqual(code, 3)
        validate.assert_not_called()
        self.assertEqual(json.loads(Path(result['report_path']).read_text())['status'], 'blocked')

    def test_remote_daemon_blocks_before_container_creation(self):
        from verification.runtime import carrier
        with patch('verification.customer.inspection.local_daemon', side_effect=ValueError('remote daemon')), \
             patch.object(carrier.docker, 'create') as create, self.assertRaisesRegex(ValueError, 'remote'):
            carrier.prepare(self.request, self.root)
        create.assert_not_called()

    def test_native_process_binds_launched_identity_and_effective_logical_ids(self):
        self.request['runtime']['kind'] = self.resource['kind'] = 'process'
        self.resource['identity'] = None
        execution = mapping.prepare(self.request, self.resource, self.root)
        self.resource['identity'] = {'pid': 100, 'pgid': 100, 'start_ticks': '1'}
        with patch.object(mapping, 'environment', return_value=execution['runtime']['env']):
            evidence = mapping.capture(execution, self.resource, self.root, {'listener_pids': [100]})
        handoff = {'resource': self.resource, 'model': self.request['model'],
                   'endpoint': self.request['service']['endpoint'], **evidence}
        _, captured, runtime = load(handoff, self.request)
        self.assertEqual(captured['resource']['identity'], self.resource['identity'])
        self.assertEqual(runtime['env'], {RT: '1,0'})
        self.assertTrue(all('docker' not in prefix for _, prefix in self.calls))


if __name__ == '__main__':
    unittest.main()
