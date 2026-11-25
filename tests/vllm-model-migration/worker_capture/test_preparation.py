"""公开 verify 入口：替代服务与设备，在独立解释器读写真实的临时采集目录。"""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
VERIFY = ROOT / 'skills/vllm-migration-execute/skills/verify-agent'
sys.path.insert(0, str(VERIFY / 'scripts'))
from verification import cli, lifecycle, runner
from verification.evidence import read_bound, write_json
from verification.runtime import execution


class CaptureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.output = self.root / '采集'
        self.cases = [{'required': True, 'protocol': 'pass', 'semantic': 'pass'}]
        self.resource = {'ownership': 'owned', 'nonce': 'owner',
                         'identity': {'container_id': 'c' * 64}}
        self.calls = []
        self.launches = []
        self.input_errors = []
        self.case_error = None
        self.dist = self.root / 'mindstudio_probe-1.3.dist-info'
        self.dist.mkdir()
        (self.dist / 'METADATA').write_text('Name: mindstudio-probe\nVersion: 1.3\n')
        package = self.root / 'msprobe' / 'pytorch'
        package.mkdir(parents=True)
        (package.parent / '__init__.py').touch()
        (package / '__init__.py').write_text('class PrecisionDebugger: pass\n')
        dump = package / 'dump'
        dump.mkdir()
        self.parser = dump / 'pt_config.py'
        self.parser.write_text(
            'import json\nfrom types import SimpleNamespace\n'
            'def parse_json_config(path, task):\n'
            '    with open(path) as stream: data = json.load(stream)\n'
            '    return SimpleNamespace(**data), SimpleNamespace(**data[data["task"]])\n')


    def request(self, kind='docker'):
        value = json.loads((VERIFY / 'assets/request.example.json').read_text())
        value['runtime'].update(kind=kind, python=sys.executable, env={'PYTHONPATH': str(self.root)})
        value['worker_capture'] = {'adapter': 'msprobe_pytorch_eager', 'output_root': str(self.output),
                                   'expected_msprobe_version': '1.3'}
        if kind == 'docker':
            # 测试目录作为容器挂载视图，两端路径不同，但指向同一目录。
            self.output.mkdir(exist_ok=True)
            self.target = self.root / 'container-view'
            if not self.target.exists():
                self.target.symlink_to(self.output, target_is_directory=True)
            value['service']['docker']['mounts'].append(
                {'source': str(self.output), 'target': str(self.target), 'read_only': False})
        else:
            del value['service']['docker']
        return value

    def public(self, request, plan=False):
        source = self.root / 'request.json'
        write_json(source, request)
        argv = ['verify.py', '--request', str(source), '--work-root', str(self.root / 'work'),
                '--run-id', 'capture-test'] + (['--plan'] if plan else [])
        output, error = StringIO(), StringIO()
        with patch.object(sys, 'argv', argv), redirect_stdout(output), redirect_stderr(error):
            code = cli.main()
        return code, json.loads(output.getvalue()) if output.getvalue() else error.getvalue()

    def target_probe(self, prefix, argv, log, **kwargs):
        self.calls.append((prefix, kwargs['env'], kwargs.get('cwd')))
        p = subprocess.run([prefix[-1], *argv], input=kwargs['input_text'], text=True,
                           capture_output=True, env=kwargs['env'], cwd=kwargs.get('cwd'), timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def run_service(self, request):
        self.resource.update(kind=request['runtime']['kind'], ownership=request['service']['ownership'])
        def snapshot(req, resource, attempt):
            handoff = {'model': req['model'], 'resource': resource, 'workers': {'fixture': True}}
            write_json(attempt / 'deployment-handoff.json', handoff)
            return handoff
        inputs = {'errors': self.input_errors, 'model': {'architectures': ['Model']}}
        with ExitStack() as stack:
            def fake(owner, name, **opts):
                return stack.enter_context(patch.object(owner, name, **opts))
            fake(runner, 'collect', return_value=inputs)
            fake(runner.carrier, 'prepare', return_value=self.resource)
            fake(runner.carrier, 'resolve_devices', side_effect=lambda r, *_: r)
            fake(runner.carrier, 'inspect_runtime', return_value={'executable_realpath': str(Path(sys.executable).resolve())})
            fake(runner.carrier, 'check_registration', return_value={})
            fake(runner.carrier, 'launch', side_effect=lambda r, *_: self.launches.append(r['service'].get('command')))
            fake(runner.carrier, 'capture', side_effect=snapshot)
            fake(runner.carrier, 'final_handoff', side_effect=lambda r, s, a, _: snapshot(r, s, a))
            fake(runner.carrier, 'failure_logs')
            fake(runner.carrier, 'stop', return_value={'state': 'stopped'})
            fake(runner.carrier, 'validate')
            fake(runner, 'execute', return_value=self.cases, side_effect=self.case_error)
            fake(execution, 'execute', side_effect=self.target_probe)
            code, result = self.public(request)
        self.assertIsInstance(result, dict, result)
        return code, json.loads(Path(result['report_path']).read_text())

    def bound_capture(self, report):
        details = report['detailed_report']
        handoff = read_bound({'path': report['evidence']['handoff'], 'sha256': details['handoff_sha256']})
        self.assertEqual(details['worker_capture'], handoff['worker_capture'])
        receipt = read_bound(handoff['worker_capture']['receipt'])
        self.assertEqual(receipt['status'], handoff['worker_capture']['status'])
        self.assertEqual(receipt['attempt_id'], Path(report['report_path']).parent.name)
        self.assertEqual(receipt['run_id'], 'capture-test')
        return handoff, receipt

    def test_plan_accepts_optional_request_and_rejects_invalid_shapes_without_output(self):
        request = self.request('process')
        self.assertEqual(self.public(request, plan=True)[0], 0)
        for key, bad in [('adapter', 'typo'), ('output_root', '../out'),
                         ('expected_msprobe_version', False), ('extra', 1)]:
            with self.subTest(key=key):
                self.assertEqual(self.public({**request, 'worker_capture': {**request['worker_capture'], key: bad}},
                                             plan=True)[0], 2)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.root / 'work').exists())

    def test_docker_and_process_config_and_final_receipt(self):
        for kind, retention in [('docker', 'keep'), ('process', 'stop')]:
            with self.subTest(kind=kind):
                request = self.request(kind)
                request['service']['retention'] = retention
                code, report = self.run_service(request)
                self.assertEqual(code, 0)
                handoff, receipt = self.bound_capture(report)
                self.assertEqual(receipt['status'], 'prepared', receipt)
                self.assertEqual(receipt['probe']['msprobe_version'], '1.3')
                self.assertEqual(receipt['probe']['executable'], sys.executable)
                config = read_bound(receipt['config'])
                self.assertEqual(config['dump_path'], receipt['dump']['runtime_path'])
                self.assertEqual((config['task'], config['level'], config['risk_level'], config['dump_enable']),
                                 ('tensor', 'mix', 'ALL', False))
                self.assertEqual(config['tensor']['data_mode'], ['forward'])
                self.assertFalse(any(Path(receipt['dump']['path']).iterdir()))
                self.assertEqual(handoff['final_service']['state'], 'retained' if kind == 'docker' else 'stopped')
                self.assertEqual(self.launches[-1], request['service']['command'])
                prefix, env, _ = self.calls[-1]
                self.assertEqual(env['PYTHONPATH'], str(self.root))
                self.assertEqual(prefix[-1], sys.executable)
                self.assertEqual('docker' in prefix, kind == 'docker')
                if kind == 'docker':
                    self.assertIn(self.resource['identity']['container_id'], prefix)
                    self.assertTrue(Path(config['dump_path']).is_relative_to(self.target))
                self.assertNotIn('worker_capture', report['conclusions'])

    def test_attempts_are_independent_and_tampering_is_detected(self):
        request = self.request('process')
        _, a = self.run_service(request)
        _, b = self.run_service(request)
        handoff, first = self.bound_capture(a)
        _, second = self.bound_capture(b)
        self.assertNotEqual(first['config']['path'], second['config']['path'])
        Path(first['config']['path']).write_text('{}')
        with self.assertRaises(ValueError):
            read_bound(first['config'])
        Path(handoff['worker_capture']['receipt']['path']).write_text('{}')
        with self.assertRaises(ValueError):
            read_bound(handoff['worker_capture']['receipt'])

    def test_unrequested_and_prerequisite_blocked(self):
        request = self.request('process')
        legacy = dict(request)
        del legacy['worker_capture']
        code, report = self.run_service(legacy)
        self.assertEqual(code, 0)
        self.assertNotIn('worker_capture', report['detailed_report'])
        self.assertNotIn('worker_capture', json.loads(Path(report['evidence']['handoff']).read_text()))
        self.input_errors.append('fixture')
        code, report = self.run_service(request)
        self.assertEqual(code, 3)
        receipt = read_bound(report['detailed_report']['worker_capture']['receipt'])
        self.assertEqual(receipt['status'], 'not_attempted')
        self.assertTrue(receipt['reason'])
        self.assertNotIn('handoff', report['evidence'])
        self.assertFalse(self.calls)

    def assert_unavailable(self, request, reason):
        previous_launches = len(self.launches)
        code, report = self.run_service(request)
        self.assertEqual(code, 0)
        _, receipt = self.bound_capture(report)
        self.assertEqual(receipt['status'], 'unavailable')
        self.assertIn(reason, receipt['reason'])
        self.assertEqual(len(self.launches), previous_launches + 1)
        return receipt

    def test_ineligible_and_conflicting_service_modes(self):
        for flag, reason in [('--no-enforce-eager', 'Eager'), ('-pp=2', 'rank'),
                             ('--additional-config.dump_config_path=/other', 'dump_config_path')]:
            with self.subTest(flag=flag):
                request = self.request('process')
                request['service']['command'].append(flag)
                self.assert_unavailable(request, reason)
        request = self.request('process')
        request['service']['command'].remove('--enforce-eager')
        self.assert_unavailable(request, 'Eager')
        request = self.request('process')
        request['service']['command'] += ['--additional-config', '{"dump_config_path":"/other"}']
        self.assert_unavailable(request, 'dump_config_path')
        request = self.request('process')
        request['service'].update(ownership='borrowed', identity={'pid': 42, 'start_ticks': '1', 'pgid': 42})
        self.assert_unavailable(request, 'borrowed')
        self.assertFalse(self.calls)

    def test_missing_readonly_and_shadowed_mounts(self):
        request = self.request()
        mounts = request['service']['docker']['mounts']
        capture_mount = mounts.pop()
        self.assert_unavailable(request, '挂载')
        mounts.append({**capture_mount, 'read_only': True})
        self.assert_unavailable(request, '挂载')
        mounts[-1] = capture_mount
        request['worker_capture']['output_root'] += '/nested'
        mounts.append({'source': str(self.root / 'other'), 'target': str(self.target / 'nested'), 'read_only': True})
        self.assert_unavailable(request, '挂载')
        self.assertFalse(self.calls)

    def test_probe_version_failure_keeps_observations_and_preparation_local(self):
        request = self.request('process')
        request['worker_capture']['expected_msprobe_version'] = '2'
        receipt = self.assert_unavailable(request, '版本')
        self.assertEqual(receipt['probe']['msprobe_version'], '1.3')
        self.dist.rename(self.root / 'hidden-metadata')
        self.assert_unavailable(request, 'mindstudio-probe')

    def test_non_directory_or_protected_output_keeps_deployment(self):
        request = self.request('process')
        self.output.write_text('original')
        self.assert_unavailable(request, str(self.output))
        self.assertEqual(self.output.read_text(), 'original')
        request['worker_capture']['output_root'] = str(VERIFY)
        self.assert_unavailable(request, '技能')
        self.assertFalse(self.calls)

    def test_prepared_does_not_mask_functional_failure(self):
        self.cases[0]['semantic'] = 'fail'
        code, report = self.run_service(self.request('process'))
        self.assertEqual(code, 1)
        self.assertFalse(report['success'])
        _, receipt = self.bound_capture(report)
        self.assertEqual(receipt['status'], 'prepared')

    def test_incompatible_native_config_and_environment_parallelism(self):
        request = self.request('process')
        request['runtime']['env']['VLLM_DP_SIZE'] = '2'
        self.assert_unavailable(request, 'rank')
        del request['runtime']['env']['VLLM_DP_SIZE']
        self.parser.write_text('def parse_json_config(path, task): raise ValueError("unsupported config")\n')
        self.assert_unavailable(request, 'unsupported config')

    def test_optional_sdk_probe_uses_target_interpreter(self):
        request = self.request('process')
        request['worker_capture']['decode_cache'] = {'max_snapshot_bytes': 4096}
        for relative, content in {
            'mindie_debugger-0.1.4.dist-info/METADATA': 'Name: mindie-debugger\nVersion: 0.1.4\n',
            'mindie_debugger/precision.py': 'class MemoryWatchpoint:\n    watch = check = lambda self: None\n',
            'msprobe/core/dump/hook_manager.py': 'class BaseHookManager: inner_switch = {}\n',
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (self.root / 'mindie_debugger/__init__.py').touch()
        code, report = self.run_service(request)
        self.assertEqual(code, 0)
        _, receipt = self.bound_capture(report)
        self.assertEqual(read_bound(receipt['decode_cache']),
                         {'status': 'available', 'version': '0.1.4', 'max_snapshot_bytes': 4096})

    def test_missing_optional_sdk_keeps_msprobe_prepared(self):
        request = self.request('process')
        request['worker_capture']['decode_cache'] = {'max_snapshot_bytes': 16777216}
        # 目标解释器显式遮蔽 SDK，宿主已安装时也覆盖缺依赖分支。
        (self.root / 'mindie_debugger.py').write_text('raise ImportError("fixture unavailable")')
        code, report = self.run_service(request)
        self.assertEqual(code, 0)
        _, receipt = self.bound_capture(report)
        config = read_bound(receipt['decode_cache'])
        self.assertEqual((receipt['status'], config['status']), ('prepared', 'unavailable'))
        self.assertEqual(config['max_snapshot_bytes'], 16777216)
        self.assertIn('fixture unavailable', config['reason'])
        self.assertIn('decode_cache/hooks.py', receipt['adapter_files'])

    def test_missing_host_readback_isolated(self):
        original = self.target_probe
        def lost_marker(prefix, argv, log, **kwargs):
            result = original(prefix, argv, log, **kwargs)
            for marker in self.output.glob('*/dump/.verify-*'):
                marker.unlink()
            return result
        self.target_probe = lost_marker
        self.assert_unavailable(self.request(), '读回')

    def test_probe_cancellation_stops_owned_service(self):
        def cancelled(*args, **kwargs):
            raise KeyboardInterrupt('cancelled fixture')
        self.target_probe = cancelled
        code, report = self.run_service(self.request('process'))
        self.assertEqual(code, 1)
        self.assertEqual(report['diagnostic']['type'], 'KeyboardInterrupt')
        self.assertEqual(report['detailed_report']['final_service']['state'], 'stopped')
        self.assertFalse(self.launches)
        ledger = {row['step']: row['status'] for row in report['detailed_report']['ledger']}
        self.assertEqual(ledger['registration'], 'succeeded')
        self.assertEqual(ledger['worker_capture'], 'failed')

    def test_receipt_write_failure_preserves_resource_cleanup(self):
        with patch.object(lifecycle, 'publish_capture', side_effect=OSError('receipt disk full')):
            code, report = self.run_service(self.request('process'))
        self.assertEqual(code, 1)
        self.assertEqual(report['detailed_report']['final_service']['state'], 'stopped')
        self.assertIn('receipt disk full', report['diagnostic']['message'])
        self.assertNotIn('worker_capture', json.loads(Path(report['evidence']['handoff']).read_text()))

    def test_incomplete_final_worker_verification_only_reports_preparation(self):
        self.case_error = RuntimeError('fixture stopped before final worker check')
        code, report = self.run_service(self.request('process'))
        self.assertEqual(code, 1)
        self.assertEqual(report['detailed_report']['worker_capture']['status'], 'prepared')
        self.assertNotIn('worker_capture', json.loads(Path(report['evidence']['handoff']).read_text()))

    def test_unrelated_submount_does_not_shadow_attempt(self):
        request = self.request()
        request['service']['docker']['mounts'].append(
            {'source': '/other', 'target': str(self.target / 'previous'), 'read_only': True})
        code, report = self.run_service(request)
        self.assertEqual(code, 0)
        _, receipt = self.bound_capture(report)
        self.assertEqual(receipt['status'], 'prepared', receipt)
