"""共享部署的运行证据、精度公开复核、清理及三文件导出集成。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import test_shared_runtime as fixtures
from devices.fixtures import write, attach
from device_contract import bound, ref


class SharedFlowTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.SharedRuntimeTests(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.request = self.case.request
        self.resource = {'kind': 'docker', 'ownership': 'owned', 'nonce': 'owner',
                         'identity': {'container_id': 'a'*64, 'image_id': 'sha256:'+'b'*64, 'started_at': 'start'}}
        self.request['_device_state'] = {'library': {}, 'executable_realpath': '/python'}
        self.external = [{'device': 1, 'pid': 20, 'chip_id': 0}]
        self.owned = [{'device': 1, 'pid': 10, 'chip_id': 0}, {'device': 2, 'pid': 11, 'chip_id': 0}]
        for pid in (10, 11, 12):
            folder = self.root / 'proc' / str(pid); folder.mkdir(parents=True)
            for name in ('cgroup', 'stat', 'cmdline'): (folder / name).write_text('synthetic')
            (folder / 'exe').symlink_to('/python')

    def identity(self, pid):
        return {'pid': pid, 'pgid': 10, 'start_ticks': str(pid)}

    def proc_path(self, *parts):
        return Path(self.root, 'proc', *parts[1:]) if parts and parts[0] == '/proc' else Path(*parts)

    def evidence(self, folder, capture=None):
        from verification.runtime import inspection
        with patch.object(inspection, 'listener_owners', return_value=[12]), \
             patch.object(inspection.inventory, 'snapshot', return_value=copy.deepcopy(self.owned + self.external)), \
             patch.object(inspection, 'same_owner', side_effect=lambda pid, resource: pid in (10, 11, 12)), \
             patch.object(inspection, 'Path', side_effect=self.proc_path), \
             patch.object(inspection, 'process_identity', side_effect=self.identity), patch.object(inspection, 'verify_library'):
            return (capture or inspection.running_evidence)(self.request, self.resource, folder)

    def test_running_evidence_separates_foreign_processes_and_confirms_only_owned(self):
        from verification.runtime import inspection, carrier
        before = self.evidence(self.root)
        self.assertEqual(before['external_npu_processes'], self.external)
        self.assertEqual([row['pid'] for row in before['npu_processes']], [10, 11])
        self.external = [{'device': 2, 'pid': 99, 'chip_id': 0}]
        after = self.evidence(self.root)
        self.assertEqual(carrier.worker_signature({'workers': before}), carrier.worker_signature({'workers': after}))
        with patch.object(inspection, 'process_identity', side_effect=self.identity) as identity, \
             patch.object(inspection, 'same_owner', return_value=True):
            inspection.confirm_workers(after, self.resource)
        self.assertEqual({call.args[0] for call in identity.call_args_list}, {10, 11, 12})

    def test_precision_before_after_uses_shared_inspection_without_capacity_or_lifecycle(self):
        from verification.devices import recheck, mapping
        from verification.runtime import inspection
        from verification.customer import npu_capacity
        sys.path.insert(0, str(fixtures.ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'))
        from precision.runtime import device_check
        handoff = {'resource': self.resource, 'model': self.request['model'], 'runtime': self.request['runtime'],
                   'endpoint': self.request['service']['endpoint'], 'workers': self.evidence(self.root)}
        declared = {k: v for k, v in self.request.items() if not k.startswith('_')}
        attach(self.root / 'mapping', declared, handoff)
        source = write(self.root / 'handoff.json', handoff)
        inputs = {'handoff': handoff, 'deployment_raw': {
            'evidence': {'handoff': source['path']}, 'detailed_report': {'handoff_sha256': source['sha256']}}}
        evidence = {k: handoff[k] for k in ('declared_request', 'device_mapping', 'effective_runtime')}
        calls = []
        original = inspection.running_evidence
        def execute(argv, folder, timeout, **kwargs):
            calls.append(argv)
            folder.mkdir()
            request = json.loads(Path(argv[argv.index('--request')+1]).read_text())
            target = Path(argv[argv.index('--work-root')+1]); target.mkdir()
            host = bound(handoff['device_mapping'])['host']
            with patch.object(recheck.carrier, 'validate'), patch.object(mapping, 'execution_host', return_value=host), \
                 patch.object(mapping, 'capture', return_value=evidence), \
                 patch.object(inspection, 'running_evidence', side_effect=lambda *args: self.evidence(args[-1], original)), \
                 patch.object(inspection, 'confirm_workers'):
                code, report = recheck.inspect(request, target, 'inspection')
            (folder / 'stdout.log').write_text(json.dumps(report))
            return {'returncode': code}
        with patch.object(device_check, 'execute', side_effect=execute), \
             patch.object(npu_capacity, 'observe') as observe, patch.object(recheck.carrier, 'stop') as stop, \
             patch.object(recheck.carrier, 'launch') as launch:
            before = device_check.inspect(inputs, self.root, 'before')
            self.external = [{'device': 2, 'pid': 99, 'chip_id': 0}]
            after = device_check.inspect(inputs, self.root, 'after')
        self.assertEqual(before, after)
        self.assertEqual(len(calls), 2)
        observe.assert_not_called(); stop.assert_not_called(); launch.assert_not_called()

    def test_failure_cleanup_only_targets_owned_container(self):
        from verification import lifecycle
        manifest = {'detailed_report': {}, 'conclusions': {}}
        with patch.object(lifecycle.carrier, 'failure_logs'), \
             patch.object(lifecycle.carrier, 'stop', return_value={'state': 'stopped'}) as stop:
            lifecycle.cleanup(self.request, self.resource, self.root, manifest, True)
        stop.assert_called_once_with(self.resource, self.root)
        self.assertEqual(manifest['conclusions']['lifecycle'], 'pass')
        borrowed = {**self.resource, 'ownership': 'borrowed'}
        with patch.object(lifecycle.carrier, 'validate'), patch.object(lifecycle.carrier, 'stop') as stop:
            lifecycle.cleanup(self.request, borrowed, self.root, manifest, True)
        stop.assert_not_called()

    def test_stage_capacity_diagnostic_becomes_user_decision_and_preserves_receipt(self):
        from customer_workflow import recovery, runner
        diagnostic = {'code': 'capacity_insufficient', 'message': '启动前不足', 'budgets': [{'shortfall_bytes': 100}]}
        report = write(self.root / 'verify.json', {'diagnostic': diagnostic})
        session = Mock()
        migration = {'status': 'blocked', 'tasks': {'verify': {'receipt': report}}}
        self.assertIsNone(runner.guarded(session, 'migration', lambda: recovery.migration_capacity(migration)))
        self.assertEqual(session.step.call_args.kwargs['decision']['capacity'], diagnostic)
        self.assertEqual(bound(report)['diagnostic'], diagnostic)
        self.assertEqual(runner.guarded(session, 'data', lambda: 'ready'), 'ready')

    def test_shared_three_json_export_keeps_actual_argv_ratio(self):
        from test_container_registration_continuation import ContainerRegistrationContinuationTests
        from tuning_handoff.generation import generate
        from tuning_handoff.cli import validated
        from tuning_handoff.flat_publication import publish
        from devices import fixtures as device_fixtures
        original_attach = device_fixtures.attach
        allocation = self.request['npu']['allocation']
        fraction = self.case.report['result']['memory_fraction']
        def shared_attach(root, declared, handoff):
            declared['npu'] = {'devices': [1, 2], 'allocation': allocation}
            declared['service']['command'] += ['--gpu-memory-utilization', str(fraction),
                                               '--tensor-parallel-size', '2', '--max-model-len', '8192']
            return original_attach(root, declared, handoff)
        case = ContainerRegistrationContinuationTests()
        with patch.object(device_fixtures, 'attach', side_effect=shared_attach):
            case.setUp()
        self.addCleanup(case.doCleanups)
        # 原夹具在 attach 之前写入 verify source；同步其显式请求绑定。
        original = json.loads(case.verify_path.read_text())['inputs']
        case.write('inputs/verify.json', {k: v for k, v in original.items() if k != 'handoffs'})
        case.migration['inputs']['verify'] = ref(case.verify_source)
        case.migration_path = case.phase('migration/migration-manifest.json', case.migration, 'migration')
        entry, _ = case.make_entry()
        configs = generate(entry, {'max_model_len': 8192, 'max_num_seqs': 1,
                                  'kv_token_capacity': 8192, 'runtime_metrics_available': False})
        request = {'schema_version': '1.0', 'entry_manifest': entry,
                   **{key: ref(case.write('shared/' + key + '.json', value)) for key, value in configs.items()}}
        checked = validated(request)
        result = publish(request, checked, case.base / 'shared-out', 'shared-out', lambda: validated(request))
        self.assertEqual(json.loads(Path(result['manifest']).read_text())['status'], 'ready')
        deployed = bound(result['deliverables']['deployment'])
        self.assertEqual(deployed['server']['gpu_memory_utilization'], str(fraction))


if __name__ == '__main__':
    unittest.main()
