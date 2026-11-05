"""共享回执经过真实文件绑定，设备命令与服务效果使用离线替身。"""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'),
               str(Path(__file__).resolve().parents[1])]
from devices.test_capacity import RAW, IDENTITY
from devices.fixtures import write, attach
from verification.customer import npu_capacity, cli
from verification.runtime import carrier, inspection
from verification.devices.capacity_table import parse
from device_contract import ref
from allocation_contract import POLICY

SPEC = {'weight_bytes': 20 * 1024**3, 'kv_bytes_per_token': 262144, 'context_tokens': 8192,
        'runtime_bytes_per_device': 4 * 1024**3, 'allowed_tp': [2], 'memory_fraction': .9}


class SharedRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        host = patch.object(npu_capacity, 'host_identity', return_value=IDENTITY)
        host.start(); self.addCleanup(host.stop)
        snapshot = patch.object(inspection.inventory, 'snapshot', return_value=[{'device': 1, 'pid': 123}])
        snapshot.start(); self.addCleanup(snapshot.stop)
        self.counter = 0
        self.request, self.report = self.fixture()

    def fixture(self, raw=RAW):
        self.counter += 1
        base = self.root / str(self.counter); base.mkdir()
        source = base / 'npu.log'; source.write_text(raw)
        observed = {'devices': parse(raw), 'host_identity': IDENTITY, 'observed_at': time.time(),
                    'npu_evidence': ref(source), 'npu_raw': raw}
        observation = write(base / 'observed.json', {'status': 'completed', 'operation': 'observe', 'result': observed})
        inputs = {'operation': 'allocate', 'policy': POLICY, 'spec': SPEC, 'requested': -1,
                  'devices': observed['devices'], 'observation': observation}
        result = npu_capacity.allocate(inputs)
        report = {'status': 'completed', 'operation': 'allocate', 'inputs': inputs, 'result': result}
        reference = write(base / 'allocation.json', report)
        request = {'npu': {'devices': result['devices'], 'allocation': reference},
                   'model': {'name': 'model', 'files': {'config.json': 'a'*64}, 'root': '/model'},
                   'runtime': {'kind': 'docker', 'python': '/python', 'env': {}, 'versions': {}, 'files': {}},
                   'service': {'ownership': 'owned', 'endpoint': 'http://127.0.0.1:8000', 'retention': 'keep',
                               'command': ['/python', '-m', 'vllm.entrypoints.openai.api_server', '--model', '/model',
                                           '--gpu-memory-utilization', str(result['memory_fraction']),
                                           '--tensor-parallel-size', '2', '--max-model-len', '8192']}}
        return request, report

    def live(self, folder, raw=RAW):
        prefix = folder / 'npu-observation.stdout.log'; prefix.write_text(raw)
        return {'devices': parse(raw), 'host_identity': IDENTITY, 'observed_at': time.time(),
                'npu_evidence': ref(prefix), 'npu_raw': raw}

    def test_public_allocate_persists_budget_and_failure_details(self):
        for fail in (False, True):
            inputs = copy.deepcopy(self.report['inputs'])
            if fail: inputs['spec']['weight_bytes'] = 1024**4
            file = self.root / ('fail.json' if fail else 'success.json'); write(file, inputs)
            argv = ['customer.py', '--request', str(file), '--work-root', str(self.root / 'cli'), '--run-id', 'allocation']
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()) as output:
                code = cli.main()
            printed = json.loads(output.getvalue())
            saved = json.loads(Path(printed['manifest']).read_text())
            self.assertEqual(code, 3 if fail else 0)
            if fail:
                self.assertEqual(saved['diagnostic']['code'], 'capacity_insufficient')
            else:
                self.assertEqual(saved['result']['sharing_mode'], 'shared')

    def test_new_policy_requires_observation_and_rejects_forged_dynamic_state(self):
        inputs = copy.deepcopy(self.report['inputs']); inputs.pop('observation')
        with self.assertRaises(ValueError): npu_capacity.allocate(inputs)
        inputs = copy.deepcopy(self.report['inputs']); inputs['devices'][0]['occupied'] = False
        with self.assertRaises(ValueError): npu_capacity.allocate(inputs)

    def test_budget_and_argv_tampering_are_rejected_before_effects(self):
        from verification.devices import allocation
        for field in ('ratio', 'devices', 'result'):
            request = copy.deepcopy(self.request)
            if field == 'ratio': request['service']['command'][6] = '.9'
            if field == 'devices': request['npu']['devices'] = [2]
            if field == 'result':
                report = copy.deepcopy(self.report); report['result']['budgets'][0]['reserve_bytes'] = 0
                request['npu']['allocation'] = write(self.root / 'tampered.json', report)
            with self.subTest(field=field), patch.object(npu_capacity, 'observe') as observe, self.assertRaises(ValueError):
                allocation.before_start(request, self.root, field)
            observe.assert_not_called()

    def test_malformed_receipts_are_validation_errors(self):
        from verification.devices import allocation
        invalid = [[], {'status': 'completed', 'operation': 'allocate', 'inputs': None, 'result': {}},
                   {**self.report, 'inputs': {**self.report['inputs'], 'spec': []}}]
        for index, report in enumerate(invalid):
            request = copy.deepcopy(self.request)
            request['npu']['allocation'] = write(self.root / ('invalid-' + str(index) + '.json'), report)
            with self.subTest(index=index), self.assertRaises(ValueError):
                allocation.shared(request)

    def test_creation_and_launch_reobserve_and_foreign_process_does_not_block(self):
        resource = {'kind': 'docker', 'ownership': 'owned', 'identity': {}, 'nonce': 'owned'}
        with patch.object(carrier, 'assert_local'), patch.object(carrier.mapping, 'execution_host'), \
             patch.object(carrier, 'listener_inodes', return_value=[]), \
             patch.object(npu_capacity, 'observe', side_effect=self.live) as observe, \
             patch.object(carrier.docker, 'create', return_value=resource) as create, \
             patch.object(carrier.docker, 'launch') as launch, patch.object(carrier, 'wait_ready', return_value={}):
            carrier.prepare(self.request, self.root)
            carrier.launch(self.request, resource, self.root)
        create.assert_called_once(); launch.assert_called_once()
        self.assertEqual(observe.call_count, 2)

    def test_capacity_drop_before_create_or_launch_prevents_effect(self):
        resource = {'kind': 'docker', 'ownership': 'owned'}
        raw = RAW.replace('21533/ 65536', '65000/ 65536')
        for stage in ('create', 'launch'):
            target = self.root / stage; target.mkdir()
            with self.subTest(stage=stage), patch.object(carrier, 'assert_local'), \
                 patch.object(carrier.mapping, 'execution_host'), patch.object(carrier, 'listener_inodes', return_value=[]), \
                 patch.object(npu_capacity, 'observe', side_effect=lambda folder: self.live(folder, raw)), \
                 patch.object(carrier.docker, 'create') as create, patch.object(carrier.docker, 'launch') as launch, \
                 patch.object(carrier, 'wait_ready', return_value={}), \
                 self.assertRaises(ValueError) as caught:
                if stage == 'create': carrier.prepare(self.request, target)
                else: carrier.launch(self.request, resource, target)
            self.assertEqual(caught.exception.diagnostic['code'], 'capacity_insufficient')
            create.assert_not_called(); launch.assert_not_called()

    def test_idle_allocation_does_not_silently_become_shared(self):
        from verification.devices import allocation
        raw = RAW.replace('| 1       0                 | 123           | VLLMEngineCor            | 18159                   |',
                          '| No running processes found in NPU 1 |')
        request, _ = self.fixture(raw)
        with patch.object(npu_capacity, 'observe', side_effect=self.live), self.assertRaises(ValueError) as caught:
            allocation.before_start(request, self.root, 'idle')
        self.assertEqual(caught.exception.diagnostic['code'], 'resources_changed')

    def test_historical_receipt_not_expired_but_host_and_raw_still_checked(self):
        from verification.devices import allocation
        with patch.object(npu_capacity.time, 'time', return_value=time.time() + 600):
            self.assertTrue(allocation.shared(self.request))
        with patch.object(npu_capacity, 'host_identity', return_value={**IDENTITY, 'boot_id': 'changed'}), \
             self.assertRaises(ValueError):
            allocation.shared(self.request)
        path = Path(self.report['inputs']['observation']['path'])
        observed = json.loads(path.read_text()); Path(observed['result']['npu_evidence']['path']).write_text('changed')
        with self.assertRaises(ValueError): allocation.shared(self.request)

    def test_worker_partition_allows_foreign_only_with_shared_policy(self):
        from verification.devices.workers import select_workers
        owned = [{'device': 1, 'pid': 10}, {'device': 2, 'pid': 11}]
        foreign = [{'device': 1, 'pid': 20}]
        owner = lambda pid: pid in (10, 11)
        self.assertEqual(select_workers(owned + foreign, [1, 2], owner, shared=True), owned)
        with self.assertRaises(ValueError): select_workers(owned + foreign, [1, 2], owner)
        with self.assertRaises(ValueError): select_workers(owned[:1] + foreign, [1, 2], owner, shared=True)
        with self.assertRaises(ValueError): select_workers(owned, [1], owner, shared=True)

    def test_external_process_exit_during_sampling_does_not_fail_shared_workers(self):
        from verification.devices.workers import select_workers
        owned = [{'device': 1, 'pid': 10}, {'device': 2, 'pid': 11}]
        observed = owned + [{'device': 1, 'pid': 20}]
        def owner(pid):
            if pid == 20: raise FileNotFoundError('external process exited')
            return True
        self.assertEqual(select_workers(observed, [1, 2], owner, shared=True), owned)
        with self.assertRaises(FileNotFoundError): select_workers(observed, [1, 2], owner)
        with self.assertRaises(ValueError): select_workers(observed[:1], [1, 2], owner, shared=True)
        for error in (ValueError('PID identity changed'), PermissionError('unreadable identity')):
            with self.subTest(error=error), self.assertRaises(type(error)):
                select_workers(observed, [1, 2], Mock(side_effect=error), shared=True)

    def test_foreign_identity_churn_is_not_a_service_identity_failure(self):
        resource = {'kind': 'process', 'identity': {'pgid': 10}}
        with patch.object(inspection, 'process_identity', side_effect=[{'pgid': 20}, {'pgid': 21}]) as identity:
            self.assertFalse(inspection.same_owner(20, resource))
            identity.assert_called_once_with(20)
        with patch.object(inspection, 'process_identity', side_effect=[{'pgid': 10}, {'pgid': 21}]), \
             self.assertRaises(ValueError):
            inspection.same_owner(10, resource)

    def test_static_handoff_binds_allocation_and_configuration_ignores_receipt_path(self):
        from device_contract import load
        from tuning_handoff.deployment import configuration
        handoff = {'resource': {'kind': 'docker', 'ownership': 'owned', 'nonce': 'owned', 'identity': {}},
                   'model': self.request['model'], 'runtime': self.request['runtime'], 'endpoint': self.request['service']['endpoint']}
        attach(self.root / 'evidence', self.request, handoff)
        self.assertEqual(load(handoff)[0]['npu']['allocation'], self.request['npu']['allocation'])
        other = copy.deepcopy(self.request)
        other['npu']['allocation'] = write(self.root / 'copied-allocation.json', self.report)
        self.assertEqual(configuration(self.request), configuration(other))
        original_path = Path(self.request['npu']['allocation']['path']); original_path.write_text('{}')
        with self.assertRaises(ValueError): load(handoff)


if __name__ == '__main__':
    unittest.main()
