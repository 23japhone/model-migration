"""性能启动前核验使用合成环境和短探测替身，不安装 EvalScope。"""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_customer_contract import customer
from customer_workflow.contract import normalize
from coordination.runtime import sources


ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='handoff 环境 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.venv = self.root / 'shared'
        self.scripts = self.venv / 'bin'
        self.scripts.mkdir(parents=True)
        self.python = str(self.scripts / 'python')
        self.cli = str(self.scripts / 'evalscope')
        for path in (self.python, self.cli):
            Path(path).write_text('synthetic; subprocess mocked')
            Path(path).chmod(0o755)
        self.install = {'operation': 'evalscope_environment', 'install': {
            'python': sys.executable, 'venv': str(self.venv)}}
        self.result = {'status': 'success', 'method': 'install_evalscope',
            'python': self.python, 'venv': str(self.venv), 'evalscope_executable': self.cli,
            'evalscope_version': '1.2.3', 'versions': {'evalscope': '1.2.3'}, 'package': 'evalscope[perf]',
            'performance_handoff': {'benchmark_python': self.python, 'evalscope_executable': self.cli,
                'evalscope_version': '1.2.3', 'env': {'PATH': str(self.scripts) + ':/old-parent'}}}
        self.manifest = {'schema_version': '1.0', 'task_id': 'vllm-model-migration-customer',
            'run_id': 'customer-1', 'inputs': normalize(customer()), 'status': 'blocked',
            'task_source': self.store('task', customer()),
            'evalscope_environment': {'python': self.python, 'version': '1.2.3'}}
        self.refresh()

    def store(self, label, value):
        path = self.root / (label + '.json')
        path.write_text(json.dumps(value))
        return sources.ref(path)

    def refresh(self):
        self.request_ref = self.store('installation', self.install)
        self.receipt_ref = self.store('installed', {'status': 'completed',
            'operation': 'evalscope_environment', 'inputs': copy.deepcopy(self.install), 'result': self.result})
        self.manifest['steps'] = {'environment': {'status': 'completed',
            'request': self.request_ref, 'receipt': self.receipt_ref}}
        self.save_manifest()

    def save_manifest(self):
        self.source = self.store('customer-manifest', self.manifest)
        self.request = self.store('request', {'customer_manifest': self.source})

    def observation(self, **changes):
        value = {'python': self.python, 'prefix': str(self.venv), 'version': '1.2.3',
                 'scripts': str(self.scripts), **changes}
        return subprocess.CompletedProcess([], 0, json.dumps(value), '')

    def invoke(self, outcomes=None, extra=()):
        from customer_workflow.evaluation import handoff_cli, handoff_probe
        from install_evalscope import REQUIRED_PERF_OPTIONS
        output = io.StringIO()
        outcomes = outcomes or [self.observation(), subprocess.CompletedProcess([], 0,
                                ' '.join(REQUIRED_PERF_OPTIONS), '')]
        with patch.object(handoff_probe.subprocess, 'run', side_effect=outcomes) as run, redirect_stdout(output):
            code = handoff_cli.main(['--request', self.request['path'], '--work-root', str(self.root / 'runs'),
                                     '--run-id', 'customer-1', *extra])
        return code, json.loads(output.getvalue()), run

    def test_success_uses_evaluation_python_current_path_and_immutable_sources(self):
        before = {p: p.read_bytes() for p in self.root.glob('*.json')}
        alternate = self.root / 'other/bin'
        alternate.mkdir(parents=True)
        (alternate / 'evalscope').write_text('wrong CLI')
        (alternate / 'evalscope').chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(alternate)}):
            code, result, run = self.invoke()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'ready')
        handoff = result['performance_handoff']
        self.assertEqual(handoff['benchmark_python'], self.python)
        self.assertNotEqual(handoff['benchmark_python'], sys.executable)
        self.assertEqual(handoff['venv'], str(self.venv))
        self.assertEqual(handoff['evalscope_executable'], self.cli)
        self.assertEqual(handoff['env']['PATH'], str(self.scripts) + os.pathsep + str(alternate))
        self.assertEqual(shutil.which('evalscope', path=handoff['env']['PATH']), self.cli)
        self.assertEqual(run.call_args_list[0].args[0][0], self.python)
        self.assertEqual(run.call_args_list[1].args[0], [self.cli, 'perf', '--help'])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['timeout'], 30)
            self.assertEqual(call.kwargs['env']['PATH'], handoff['env']['PATH'])
        self.assertEqual(result['sources']['customer_manifest'], self.source)
        self.assertEqual(len(list((self.root / 'runs').rglob('*.process.json'))), 2)
        self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))

    def test_new_and_historical_handoff_venv_requires_consistent_evidence(self):
        self.result['performance_handoff']['venv'] = str(self.venv)
        self.refresh()
        self.assertEqual(self.invoke()[0], 0)
        self.result['performance_handoff']['venv'] = '/other'
        self.refresh()
        code, result, run = self.invoke()
        self.assertEqual(code, 3, result)
        run.assert_not_called()

    def test_invalid_or_incomplete_receipts_never_probe(self):
        original = copy.deepcopy(self.manifest)
        mutations = [lambda v: v.update(run_id='wrong'),
            lambda v: v['evalscope_environment'].update(invalidated=True),
            lambda v: v['steps']['environment'].update(status='not_run'),
            lambda v: v['steps']['environment'].pop('request'),
            lambda v: v.pop('evalscope_environment'),
            lambda v: v.update(evalscope_install_selection={'options': {}, 'source': self.request_ref})]
        for mutate in mutations:
            self.manifest = copy.deepcopy(original)
            mutate(self.manifest)
            self.save_manifest()
            code, result, run = self.invoke()
            self.assertEqual(code, 3, result)
            self.assertNotIn('performance_handoff', result)
            run.assert_not_called()

    def test_result_request_binding_and_handoff_must_agree(self):
        for field, wrong in [('python', sys.executable), ('venv', '/wrong'),
                             ('evalscope_version', '2.0'), ('package', 'evalscope[perf]==2')]:
            with self.subTest(field=field):
                original = self.result[field]
                self.result[field] = wrong
                self.refresh()
                code, result, run = self.invoke()
                self.assertEqual(code, 3, result)
                run.assert_not_called()
                self.result[field] = original
        self.install['install']['dry_run'] = True
        self.refresh()
        self.assertEqual(self.invoke()[0], 3)

    def test_python_prefix_version_and_scripts_drift_require_new_run(self):
        for field, wrong in [('python', sys.executable), ('prefix', '/other'),
                             ('version', '2.0'), ('version', None), ('scripts', '/other/bin')]:
            code, result, run = self.invoke([self.observation(**{field: wrong})])
            self.assertEqual(code, 3, result)
            self.assertTrue(result['new_run_required'])
            self.assertEqual(result['reason_kind'], 'identity_changed')
            run.assert_called_once()
            self.assertNotIn('invalidated', sources.bound(self.source)['evalscope_environment'])

    def test_missing_python_or_cli_blocks_without_using_alternative(self):
        for path in (self.python, self.cli):
            original = Path(path).read_bytes()
            Path(path).unlink()
            code, result, _ = self.invoke()
            self.assertEqual(code, 3, result)
            self.assertTrue(result['new_run_required'])
            Path(path).write_bytes(original)
            Path(path).chmod(0o755)

    def test_wrong_path_resolution_blocks_before_cli_help(self):
        from customer_workflow.evaluation import handoff_probe
        with patch.object(handoff_probe.shutil, 'which', return_value='/wrong/evalscope'):
            code, result, _ = self.invoke()
        self.assertEqual(code, 3, result)
        self.assertNotIn('performance_handoff', result)

    def test_unknown_failures_preserve_diagnostics_and_original_binding(self):
        before = Path(self.source['path']).read_bytes()
        for outcome in (subprocess.TimeoutExpired(['python'], 30, output='partial', stderr='timeout'),
                        PermissionError('denied'), subprocess.CompletedProcess([], 1, '', 'error'),
                        subprocess.CompletedProcess([], 0, 'not-json', ''),
                        subprocess.CompletedProcess([], 0, '{}', '')):
            code, result, _ = self.invoke([outcome])
            self.assertEqual(code, 3, result)
            self.assertFalse(result['new_run_required'])
            self.assertEqual(result['reason_kind'], 'unverifiable')
            self.assertTrue(Path(result['manifest']).is_file())
        self.assertEqual(Path(self.source['path']).read_bytes(), before)
        self.assertTrue(any('partial' in p.read_text() for p in (self.root / 'runs').rglob('*.stdout.log')))

    def test_cli_help_failure_and_timeout_are_unverifiable(self):
        for outcome in (subprocess.CompletedProcess([], 0, 'missing perf options', ''),
                        subprocess.TimeoutExpired([self.cli], 30)):
            code, result, run = self.invoke([self.observation(), outcome])
            self.assertEqual(code, 3, result)
            self.assertEqual(result['reason_kind'], 'unverifiable')
            self.assertEqual(run.call_count, 2)

    def test_plan_only_validates_shape_and_never_reads_references(self):
        from customer_workflow.evaluation import handoff_cli
        self.request = self.store('request', {'customer_manifest': {'path': '/missing/customer.json', 'sha256': '0'*64}})
        with patch.object(handoff_cli.sources, 'bound', side_effect=AssertionError('reference read')):
            code, result, run = self.invoke(extra=['--plan'])
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'not_run')
        run.assert_not_called()
        self.assertFalse((self.root / 'runs').exists())

    def test_output_must_not_overlap_venv_or_sources(self):
        code, result, run = self.invoke(extra=['--work-root', str(self.venv)])
        self.assertEqual(code, 2, result)
        run.assert_not_called()
        self.assertFalse((self.venv / 'customer-1').exists())

    def test_changed_reference_never_probes(self):
        Path(self.receipt_ref['path']).write_text('{}')
        code, result, run = self.invoke()
        self.assertEqual(code, 3, result)
        run.assert_not_called()

    def test_malformed_nested_history_blocks_without_traceback(self):
        original = copy.deepcopy(self.manifest)
        for wrong in ([], None, 'invalid'):
            self.manifest = {**original, 'steps': wrong}
            self.save_manifest()
            code, result, run = self.invoke()
            self.assertEqual(code, 3, result)
            run.assert_not_called()
        self.manifest = original
        self.result['versions'] = []
        self.refresh()
        self.assertEqual(self.invoke()[0], 3)

    def test_customer_snapshot_and_final_backend_must_agree_with_handoff(self):
        original = copy.deepcopy(self.manifest)
        self.manifest['inputs']['model']['model_name'] = 'different-task'
        self.save_manifest()
        code, result, run = self.invoke()
        self.assertEqual(code, 3, result)
        run.assert_not_called()
        self.manifest = original
        self.manifest['effective_inputs'] = copy.deepcopy(self.manifest['inputs'])
        self.manifest['effective_inputs']['accuracy']['evaluator'] = 'aisbench'
        self.save_manifest()
        code, result, run = self.invoke()
        self.assertEqual(code, 3, result)
        run.assert_not_called()

    def test_default_venv_handoff_is_recovered_from_receipt_not_control_python(self):
        from customer_workflow.evaluation import handoff
        self.install['install']['venv'] = '/data/evalscope-venv'
        self.result.update(venv='/data/evalscope-venv', python='/data/evalscope-venv/bin/python',
                           evalscope_executable='/data/evalscope-venv/bin/evalscope')
        self.result['performance_handoff'].update(benchmark_python=self.result['python'],
                                                 evalscope_executable=self.result['evalscope_executable'])
        self.manifest['evalscope_environment']['python'] = self.result['python']
        self.refresh()
        expected, _, _ = handoff.load({'customer_manifest': self.source}, 'customer-1')
        self.assertEqual(expected['venv'], '/data/evalscope-venv')
        self.assertEqual(expected['benchmark_python'], self.result['python'])
        self.assertNotEqual(expected['benchmark_python'], self.install['install']['python'])

    def test_unknown_fields_and_bad_reference_shape_are_invalid_without_writes(self):
        for value in ({}, {'customer_manifest': self.source, 'env': {}},
                      {'customer_manifest': {'path': 'relative', 'sha256': '0'*64}}):
            self.request = self.store('request', value)
            code, result, run = self.invoke()
            self.assertEqual(code, 2, result)
            run.assert_not_called()
        self.assertFalse((self.root / 'runs').exists())

    def test_change_during_probe_cannot_publish_ready(self):
        from install_evalscope import REQUIRED_PERF_OPTIONS
        calls = []
        def observe(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                Path(self.source['path']).write_text('{}')
                return self.observation()
            return subprocess.CompletedProcess([], 0, ' '.join(REQUIRED_PERF_OPTIONS), '')
        code, result, run = self.invoke(observe)
        self.assertEqual(code, 3, result)
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('performance_handoff', result)

    def test_cancelled_probe_stops_and_saves_process_evidence(self):
        code, result, run = self.invoke([KeyboardInterrupt()])
        self.assertEqual(code, 1, result)
        self.assertEqual(result['reason_kind'], 'cancelled')
        run.assert_called_once()
        process = next((self.root / 'runs').rglob('*.process.json'))
        self.assertTrue(json.loads(process.read_text())['cancelled'])

    def test_evidence_write_failure_stops_before_probe(self):
        from customer_workflow.evaluation import handoff_cli
        from coordination.runtime.evidence import EvidenceError
        with patch.object(handoff_cli, 'write', side_effect=EvidenceError('cannot save')):
            code, result, run = self.invoke()
        self.assertEqual(code, 1, result)
        run.assert_not_called()

    def test_relocated_entry_and_synthetic_consumer_receive_same_environment(self):
        from install_evalscope import REQUIRED_PERF_OPTIONS
        copied = self.root / '搬迁 完整 Skill'
        shutil.copytree(ROOT, copied)
        # 合成可执行文件模拟观察协议；无真实 venv、SDK 或性能负载。
        python_code = ('#!' + sys.executable + '\nimport json,os,sys,shutil\n'
            + 'if "-c" in sys.argv:\n    print(' + repr(self.observation().stdout) + ')\n'
            + 'else:\n    print(json.dumps({"python":sys.argv[0],"PATH":os.environ["PATH"],'
              '"cli":shutil.which("evalscope")}))\n')
        Path(self.python).write_text(python_code)
        Path(self.cli).write_text('#!' + sys.executable + '\nprint(' + repr(' '.join(REQUIRED_PERF_OPTIONS)) + ')\n')
        environment = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        environment.update(PYTHONDONTWRITEBYTECODE='1', PATH='/parent-only')
        before = {p: p.read_bytes() for p in self.root.glob('*.json')}
        result = subprocess.run([sys.executable, '-B', str(copied / 'scripts/check_evalscope_handoff.py'),
            '--request', self.request['path'], '--work-root', str(self.root / 'relocated-runs'),
            '--run-id', 'customer-1'], env=environment, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        handoff = json.loads(result.stdout)['performance_handoff']
        consumed = subprocess.run([handoff['benchmark_python'], 'synthetic-consumer'],
            env={**environment, **handoff['env']}, capture_output=True, text=True, timeout=30)
        self.assertEqual(consumed.returncode, 0, consumed.stderr)
        observed = json.loads(consumed.stdout)
        self.assertEqual(observed, {'python': self.python, 'PATH': handoff['env']['PATH'], 'cli': self.cli})
        self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))


if __name__ == '__main__':
    unittest.main()
