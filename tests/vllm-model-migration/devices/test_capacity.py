"""最新容量观察及客户编排回归；只模拟主机命令，不访问硬件。"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))

# 与实机 25.5.1 相同的列布局，数值和进程为合成夹具。
RAW = """+------------------------------------------------------------------------------------------------+
| npu-smi 25.5.1                   Version: 25.5.1                                               |
+---------------------------+---------------+----------------------------------------------------+
| NPU   Name                | Health        | Power(W)    Temp(C)           Hugepages-Usage(page)|
| Chip                      | Bus-Id        | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)        |
+===========================+===============+====================================================+
| 1     910B2               | OK            | 176.3       50                0    / 0             |
| 0                         | 0000:01:00.0  | 44          0    / 0          21533/ 65536         |
+===========================+===============+====================================================+
| 2     910B2               | OK            | 101.1       46                0    / 0             |
| 0                         | 0000:C2:00.0  | 0           0    / 0          3418 / 65536         |
+===========================+===============+====================================================+
| NPU     Chip              | Process id    | Process name             | Process memory(MB)      |
+===========================+===============+====================================================+
| 1       0                 | 123           | VLLMEngineCor            | 18159                   |
+===========================+===============+====================================================+
| No running processes found in NPU 2                                                            |
+===========================+===============+====================================================+
"""
SPEC = {'weight_bytes': 1000, 'kv_bytes_per_token': 2, 'context_tokens': 100,
        'runtime_bytes_per_device': 100, 'allowed_tp': [1, 2], 'memory_fraction': .9}
IDENTITY = {'hostname': 'synthetic-host', 'boot_id': 'synthetic-boot'}


class CapacityTableTests(unittest.TestCase):
    def test_known_table_preserves_nonzero_ids_and_uses_hbm(self):
        from verification.devices.capacity_table import parse
        for raw in (RAW, RAW.replace('\n', '  \r\n')):
            with self.subTest(crlf='\r' in raw):
                self.assertEqual(parse(raw), [
                    {'id': 1, 'total_bytes': 65536 * 1048576, 'free_bytes': 44003 * 1048576, 'occupied': True},
                    {'id': 2, 'total_bytes': 65536 * 1048576, 'free_bytes': 62118 * 1048576, 'occupied': False}])

    def test_unknown_incomplete_and_contradictory_tables_are_rejected(self):
        from verification.devices.capacity_table import parse
        invalid = [RAW.replace('HBM-Usage(MB)', 'HBM-Usage(GB)'), RAW.replace('910B2', '310P'),
                   RAW.replace('21533/ 65536', 'N/A / 65536'), RAW.replace('21533/ 65536', '70000/ 65536'),
                   RAW.replace('3418 / 65536', '0 / 0'), RAW.replace('| OK ', '| Alarm '),
                   RAW.replace('| 2     910B2', '| 1     910B2'),
                   RAW.replace('Process id', 'PID'), RAW.replace('| 1       0', '| 1       1'),
                   RAW.replace('NPU 2 ', 'NPU 9 '), RAW.replace('| 123 ', '| N/A '),
                   RAW[:RAW.index('| No running')], RAW + '| No running processes found in NPU 1 |\n',
                   RAW.replace('| 0                         | 0000:C2:00.0  | 0           0    / 0          3418 / 65536         |', '')]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse(raw)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def observe(self):
        from verification.customer import npu_capacity
        def command(argv, prefix, timeout):
            self.assertEqual(argv, ['npu-smi', 'info'])
            Path(str(prefix) + '.stdout.log').write_text(RAW)
            return RAW
        with patch.object(npu_capacity, 'host_identity', return_value=IDENTITY), \
                patch.object(npu_capacity, 'run', side_effect=command):
            return npu_capacity.observe(self.root)

    def request(self):
        from verification.evidence import sha256
        host = self.observe()
        report = self.root / 'observation.json'
        report.write_text(json.dumps({'status': 'completed', 'operation': 'observe', 'result': host}))
        return {'spec': SPEC, 'requested': -1, 'devices': host['devices'],
                'observation': {'path': str(report), 'sha256': sha256(report)}}

    def test_capture_binds_raw_hash_time_and_host_then_allocates(self):
        from verification.customer import npu_capacity
        request = self.request()
        with patch.object(npu_capacity, 'host_identity', return_value=IDENTITY):
            self.assertEqual(npu_capacity.allocate(request)['devices'], [2])
        report = json.loads(Path(request['observation']['path']).read_text())['result']
        self.assertEqual(report['host_identity'], IDENTITY)
        self.assertLess(abs(time.time() - report['observed_at']), 5)
        self.assertEqual(Path(report['npu_evidence']['path']).read_text(), RAW)

    def test_mismatch_expiry_and_evidence_tampering_block(self):
        from verification.customer import npu_capacity
        from verification.evidence import sha256
        for case in ('host', 'expired', 'future', 'raw', 'report', 'devices', 'missing_source'):
            with self.subTest(case=case):
                request = self.request()
                host = {**IDENTITY, 'boot_id': 'other'} if case == 'host' else IDENTITY
                path = Path(request['observation']['path']); report = json.loads(path.read_text())
                if case in ('expired', 'future'):
                    report['result']['observed_at'] += -120 if case == 'expired' else 120
                if case == 'missing_source':
                    report['result'].pop('npu_evidence')
                if case in ('expired', 'future', 'missing_source'):
                    path.write_text(json.dumps(report)); request['observation']['sha256'] = sha256(path)
                if case == 'raw':
                    Path(report['result']['npu_evidence']['path']).write_text(RAW + 'changed')
                if case == 'report':
                    path.write_text('{}')
                if case == 'devices':
                    request['devices'][0]['occupied'] = False
                with patch.object(npu_capacity, 'host_identity', return_value=host), self.assertRaises(ValueError):
                    npu_capacity.allocate(request)


    def test_malformed_observation_report_blocks(self):
        from verification.customer import npu_capacity
        from verification.evidence import sha256
        for invalid in ([], {'status': 'completed', 'operation': 'observe', 'result': []}):
            request = self.request()
            path = Path(request['observation']['path']); path.write_text(json.dumps(invalid))
            request['observation']['sha256'] = sha256(path)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                npu_capacity.allocate(request)

    def test_public_observe_and_allocate_bind_evidence_and_keep_legacy_allocate(self):
        from verification.customer import cli, npu_capacity, inspection
        from verification.evidence import sha256
        def command(argv, prefix, timeout):
            value = RAW if argv[0] == 'npu-smi' else json.dumps({'OSType': 'linux', 'Name': 'host', 'ID': 'daemon'})
            Path(str(prefix) + '.stdout.log').write_text(value)
            return value
        def invoke(request):
            source = self.root / 'request.json'; source.write_text(json.dumps(request))
            output = io.StringIO()
            argv = ['customer.py', '--request', str(source), '--work-root', str(self.root / 'evidence'), '--run-id', 'fresh']
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
                code = cli.main()
            return code, json.loads(output.getvalue())
        def path(value):
            return Mock(read_text=Mock(return_value='MemTotal: 1')) if value == '/proc/meminfo' else Path(value)
        with patch.object(npu_capacity, 'host_identity', return_value=IDENTITY), \
                patch.object(npu_capacity, 'run', side_effect=command), \
                patch.object(inspection, 'run', side_effect=command), \
                patch.object(inspection.platform, 'system', return_value='Linux'), \
                patch.object(inspection, 'local_daemon'), patch.object(inspection, 'free_port', return_value=8000), \
                patch.object(inspection.os, 'sched_getaffinity', return_value={0}, create=True), \
                patch.object(inspection, 'Path', side_effect=path):
            code, observed = invoke({'operation': 'observe', 'capacity': True, 'host': '127.0.0.1', 'port': 8000})
            self.assertEqual(code, 0, observed)
            request = {'operation': 'allocate', 'spec': SPEC, 'requested': -1, 'devices': observed['result']['devices'],
                       'observation': {'path': observed['manifest'], 'sha256': sha256(observed['manifest'])}}
            code, allocation = invoke(request)
            self.assertEqual((code, allocation['result']['devices']), (0, [2]))
            self.assertEqual(allocation['inputs']['observation'], request['observation'])
            Path(observed['result']['npu_evidence']['path']).write_text('changed')
            code, failure = invoke(request)
            self.assertEqual(code, 3)
            self.assertIn('摘要变化', failure['reason'])
            self.assertTrue(Path(failure['manifest']).exists())
            request.pop('observation')
            self.assertEqual(invoke(request)[0], 0)

    def test_host_change_during_capture_and_parse_failure_keep_raw_log(self):
        from verification.customer import npu_capacity
        for raw, hosts in ((RAW, [IDENTITY, {**IDENTITY, 'boot_id': 'new'}]), ('unknown', [IDENTITY, IDENTITY])):
            def command(argv, prefix, timeout):
                Path(str(prefix) + '.stdout.log').write_text(raw)
                return raw
            with self.subTest(raw=raw), patch.object(npu_capacity, 'host_identity', side_effect=hosts), \
                    patch.object(npu_capacity, 'run', side_effect=command), self.assertRaises(ValueError):
                npu_capacity.observe(self.root)
            self.assertEqual((self.root / 'npu-observation.stdout.log').read_text(), raw)


class WorkflowTests(unittest.TestCase):
    def migrate(self, host, events, session=None):
        from customer_workflow.runner import migrate
        from verification.customer.capacity import select
        session = session or Mock(value={'steps': {'preparation': {'receipt': {'path': '/prepared'}}}})
        session.ready.side_effect = lambda name: {'ready': True} if name == 'preparation' else None
        session.artifact.return_value = {'path': '/compiled'}
        recipe = {'devices': [{'id': 1, 'free_bytes': 900, 'total_bytes': 1000, 'occupied': False}],
                  'model_max_context': 100, 'max_num_seqs': 1, 'runtime_metrics_available': False}
        def operation(s, key, owner, request, work_root):
            events.append(key)
            if key == 'host_observation':
                self.assertIs(request['capacity'], True)
                s.value['steps'][key] = {'receipt': {'path': '/fresh', 'sha256': 'a' * 64}}
                if isinstance(host, Exception): raise host
                return host
            self.assertEqual(request['observation'], s.value['steps']['host_observation']['receipt'])
            result = select(request['spec'], request['devices'], request['requested'], policy=request.get('policy'))
            s.value['steps']['allocation'] = {'receipt': {'path': '/allocation', 'sha256': 'b' * 64}}
            return result
        with patch('customer_workflow.effects.precheck_plan', side_effect=lambda *a: events.append('precheck') or {}), \
                patch('customer_workflow.effects.functional_inputs', return_value=({}, 100)), \
                patch('customer_workflow.runner.deployment.prepared_model', return_value={}), \
                patch('customer_workflow.runner.deployment.capacity_spec', return_value=SPEC), \
                patch('customer_workflow.runner.deployment.precheck', return_value={}), \
                patch('customer_workflow.runner.deployment.verify', return_value={'npu': {}, 'service': {'endpoint': 'http://127.0.0.1:8000'}}), \
                patch('customer_workflow.runner.effects.operation', side_effect=operation), \
                patch('customer_workflow.runner.recovery.container', side_effect=lambda *a: events.append('container')), \
                patch('customer_workflow.runner.execute', side_effect=lambda *a: events.append('deploy') or {}):
            return migrate(session, {'environment': {'available_npu_devices': [1], 'container_name': 'model'}},
                           {'deployment': recipe}, [], Path('/unused'), {})

    def test_new_occupied_or_insufficient_observation_blocks_before_container(self):
        for occupied, free in ((True, 900), (False, 50)):
            events = []
            host = {'devices': [{'id': 1, 'free_bytes': free, 'total_bytes': 1000, 'occupied': occupied}], 'port': 8000}
            with self.subTest(occupied=occupied), self.assertRaises(ValueError):
                self.migrate(host, events)
            self.assertEqual(events, ['precheck', 'host_observation', 'allocation'])

    def test_failed_resume_observes_again(self):
        session = Mock(value={'steps': {'preparation': {'receipt': {'path': '/prepared'}}}})
        events = []
        row = {'id': 1, 'free_bytes': 900, 'total_bytes': 1000, 'occupied': True}
        with self.assertRaises(ValueError):
            self.migrate({'devices': [row], 'port': 8000}, events, session)
        # 同一会话的下一次新部署尝试必须重新观察；新空闲仍须满足容量。
        row = {**row, 'occupied': False, 'free_bytes': 9000, 'total_bytes': 10000}
        self.migrate({'devices': [row], 'port': 8000}, events, session)
        self.assertEqual(events, ['precheck', 'host_observation', 'allocation', 'precheck', 'host_observation', 'allocation', 'container', 'deploy'])

    def test_observation_failure_stops_only_deployment_branch(self):
        from customer_workflow.runner import guarded
        events = []; session = Mock()
        self.assertIsNone(guarded(session, 'migration', lambda: self.migrate(ValueError('观察失败'), events)))
        self.assertEqual(events, ['precheck', 'host_observation'])
        self.assertEqual(session.step.call_args.kwargs['diagnostic']['message'], '观察失败')
        self.assertEqual(guarded(session, 'independent-data', lambda: 'prepared'), 'prepared')

    def test_missing_selected_card_blocks_before_container(self):
        events = []
        with self.assertRaisesRegex(ValueError, '缺少实际观察'):
            self.migrate({'devices': [], 'port': 8000}, events)
        self.assertEqual(events, ['precheck', 'host_observation'])

    def test_lower_level_creation_still_rechecks_live_processes(self):
        from verification.runtime import carrier, inspection
        request = {'service': {'ownership': 'owned', 'endpoint': 'http://127.0.0.1:8000'},
                   'runtime': {'kind': 'docker'}, 'npu': {'devices': [1]}}
        with patch.object(carrier, 'assert_local'), patch.object(carrier.mapping, 'execution_host'), \
                patch.object(carrier, 'listener_inodes', return_value=[]), \
                patch.object(inspection.inventory, 'snapshot', return_value=[{'device': 1, 'pid': 123}]), \
                patch.object(carrier.docker, 'create') as create, self.assertRaisesRegex(ValueError, '计算进程'):
            carrier.prepare(request, Path('/unused'))
        create.assert_not_called()

    def test_workflow_accepts_legacy_dynamic_change_but_keeps_static_freeze(self):
        from customer_workflow.runner import workflow
        old = {'dtype': 'bfloat16', 'devices': [{'occupied': False}]}
        new = {'dtype': 'bfloat16', 'devices': [{'occupied': True}]}
        session = Mock(value={'resolved_observations': {'deployment': old}})
        with patch('customer_workflow.runner.discover', side_effect=ValueError('after_binding')) as discover:
            with self.assertRaisesRegex(ValueError, 'after_binding'):
                workflow(session, {}, {'observations': {'deployment': new}}, Path('/unused'), None)
            self.assertEqual(discover.call_args.args[2]['deployment'], {'dtype': 'bfloat16'})
            self.assertEqual(old['devices'], [{'occupied': False}])
            self.assertEqual(new['devices'], [{'occupied': True}])
            discover.reset_mock()
            with self.assertRaisesRegex(ValueError, '已冻结'):
                workflow(session, {}, {'observations': {'deployment': {**new, 'dtype': 'float16'}}}, Path('/unused'), None)
            discover.assert_not_called()

    def test_frozen_recipe_excludes_dynamic_devices_but_rejects_static_change(self):
        from customer_workflow.recovery import freeze
        session = Mock(value={'resolved_observations': {'deployment': {'dtype': 'bfloat16', 'devices': []}}})
        freeze(session, 'deployment', {'dtype': 'bfloat16', 'devices': [{'occupied': False}]})
        freeze(session, 'deployment', {'dtype': 'bfloat16', 'devices': [{'occupied': True}]})
        self.assertEqual(session.value['resolved_observations']['deployment'], {'dtype': 'bfloat16'})
        with self.assertRaises(ValueError):
            freeze(session, 'deployment', {'dtype': 'float16'})


if __name__ == '__main__':
    unittest.main()
