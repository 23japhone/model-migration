"""EvalScope 共享安装协议的离线验收；不运行 pip，不触达共享 venv。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_customer_contract import customer, SCRIPTS

PREPARE = SCRIPTS.parent / 'skills/vllm-migration-prepare/scripts'
sys.path.insert(0, str(PREPARE))


def installed(root, version='1.2.3'):
    return {'status': 'success', 'python': str(root / 'bin/python'), 'venv': str(root),
            'evalscope_executable': str(root / 'bin/evalscope'),
            'evalscope_version': version, 'package': 'evalscope[perf]'}


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.venv = self.root / 'shared'
        (self.venv / 'bin').mkdir(parents=True)
        cli = self.venv / 'bin/evalscope'
        cli.write_text('#!/bin/sh\nexit 0\n')
        cli.chmod(0o755)
        self.attempt = self.root / 'runs/attempt'
        self.attempt.mkdir(parents=True)

    def invoke(self, result=None, code=0, prefix='', **options):
        from customer_assets import evaluator
        run = subprocess.CompletedProcess([], code, prefix + json.dumps(result or installed(self.venv), indent=2), 'pip stderr')
        with patch('customer_assets.environment.call', return_value=run) as call:
            value = evaluator.install({'operation': 'evalscope_environment',
                                       'install': {'venv': str(self.venv), **options}}, self.attempt)
        return value, call

    def test_verbatim_source_is_vendored(self):
        path = SCRIPTS / 'install_evalscope.py'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         '3b2aed60e2b73a7a8ae3a1db3f33a0219d9bf2159177303fa953ff9733ebadef')

    def test_defaults_and_all_parameters(self):
        from evalscope_installation import normalize, arguments
        options = normalize({})
        self.assertEqual(options['python'], sys.executable)
        self.assertEqual(options['venv'], '/data/evalscope-venv')
        argv = arguments(normalize({'python': '/tmp/python with space', 'venv': str(self.venv),
                                   'version': '1.2.3', 'upgrade': True, 'dry_run': True, 'help': True}))
        self.assertEqual(argv, ['--python', '/tmp/python with space', '--venv', str(self.venv),
                                '--version', '1.2.3', '--upgrade', '--dry-run', '--help'])
        self.assertNotIn('--upgrade', arguments(options))

    def test_types_unknown_fields_and_overlap_rejected(self):
        from evalscope_installation import normalize, protect
        for bad in (None, [], {'python': -1}, {'venv': ''}, {'version': None}, {'upgrade': 1},
                    {'dry_run': 'true'}, {'help': 0}, {'candidates': []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize(bad)
        for path in (self.root, self.attempt, self.attempt / 'nested'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                protect(normalize({'venv': str(path)}), [self.attempt])
        with self.assertRaises(ValueError):
            protect(normalize({'venv': str(SCRIPTS / 'venv')}), [])

    def test_mixed_logs_adapt_version_preserve_launch_path_and_save_evidence(self):
        value, call = self.invoke(prefix='Collecting packages\n{not json}\n')
        self.assertEqual(value['python'], str(self.venv / 'bin/python'))
        self.assertEqual(value['versions'], {'evalscope': '1.2.3'})
        self.assertEqual(value['evalscope_executable'], str(self.venv / 'bin/evalscope'))
        self.assertEqual(value['performance_handoff']['benchmark_python'], value['python'])
        self.assertEqual(value['performance_handoff']['venv'], value['venv'])
        self.assertEqual(value['performance_handoff']['env']['PATH'].split(':')[0], str(self.venv / 'bin'))
        self.assertEqual(call.call_args.args[1], 1800)
        record = json.loads((self.attempt / 'evalscope-install.process.json').read_text())
        self.assertEqual(record['returncode'], 0)
        self.assertIn(str(SCRIPTS / 'install_evalscope.py'), record['argv'])
        self.assertIn('Collecting packages', (self.attempt / 'evalscope-install.stdout.log').read_text())
        self.assertEqual((self.attempt / 'evalscope-install.stderr.log').read_text(), 'pip stderr')

    def test_bad_exit_missing_json_and_wrong_environment_block(self):
        from customer_assets import evaluator
        with self.assertRaises(ValueError):
            self.invoke(code=1)
        for output in ('pip log only', '{} trailing', '{"status":"success"}', '[]'):
            with self.subTest(output=output), patch('customer_assets.environment.call',
                    return_value=subprocess.CompletedProcess([], 0, output, '')), self.assertRaises(ValueError):
                evaluator.install({'install': {'venv': str(self.venv)}}, self.attempt)
        for field, bad in (('python', sys.executable), ('venv', '/other'),
                           ('evalscope_executable', '/other/evalscope'), ('evalscope_version', ''),
                           ('status', 'planned'), ('package', 'other')):
            result = {**installed(self.venv), field: bad}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.invoke(result)

    def test_trailing_json_can_follow_unterminated_log_line(self):
        value, _ = self.invoke(prefix='pip progress without final newline: ')
        self.assertEqual(value['versions']['evalscope'], '1.2.3')

    def test_timeout_keeps_partial_logs_no_retry(self):
        from customer_assets import evaluator
        error = subprocess.TimeoutExpired(['python'], 1800, output='partial', stderr='err')
        with patch('customer_assets.environment.call', side_effect=error) as call, self.assertRaises(subprocess.TimeoutExpired):
            evaluator.install({'install': {'venv': str(self.venv)}}, self.attempt)
        self.assertEqual(call.call_count, 1)
        self.assertEqual((self.attempt / 'evalscope-install.stdout.log').read_text(), 'partial')
        self.assertTrue(json.loads((self.attempt / 'evalscope-install.process.json').read_text())['timed_out'])

    def test_help_and_dry_run_do_not_claim_ready(self):
        from customer_assets import evaluator
        with patch('customer_assets.environment.call', return_value=subprocess.CompletedProcess([], 0, 'usage: install', '')):
            result = evaluator.install({'install': {'help': True}}, self.attempt)
        self.assertEqual(result['status'], 'not_run')
        planned = {'status': 'planned', 'python': str(self.venv / 'bin/python'),
                   'venv': str(self.venv), 'command': ['preview']}
        result, _ = self.invoke(planned, dry_run=True)
        self.assertEqual(result['status'], 'not_run')
        self.assertNotIn('versions', result)

    def test_explicit_version_and_all_options_reach_subprocess(self):
        result = {**installed(self.venv), 'package': 'evalscope[perf]==1.2.3'}
        value, call = self.invoke(result, python='/base/python with spaces', version='1.2.3', upgrade=True)
        self.assertEqual(value['versions']['evalscope'], '1.2.3')
        self.assertEqual(call.call_args.args[0][-7:], ['--python', '/base/python with spaces', '--venv',
                                                     str(self.venv), '--version', '1.2.3', '--upgrade'])
        with self.assertRaisesRegex(ValueError, '实际 EvalScope 版本'):
            self.invoke({**result, 'evalscope_version': '2.0.0'}, version='1.2.3')

    def test_cli_path_resolution_and_venv_python_symlink(self):
        (self.venv / 'bin/python').symlink_to(sys.executable)
        value, _ = self.invoke()
        self.assertEqual(value['python'], str(self.venv / 'bin/python'))
        with patch('customer_assets.evaluator.shutil.which', return_value='/other/evalscope'), self.assertRaisesRegex(ValueError, 'PATH'):
            self.invoke()

    def test_accuracy_request_and_performance_handoff_share_actual_environment(self):
        from customer_workflow import runner
        from customer_workflow.contract import normalize
        from customer_workflow.compile.accuracy import request
        with patch('customer_assets.environment.resolve_versions', side_effect=AssertionError('latest')):
            result, _ = self.invoke()
        task = normalize(customer())
        session = Mock()
        observations = {'model_resolution': {}}
        with patch.object(runner.effects, 'evaluator', return_value=result):
            runner.discover(session, task, observations, self.root / 'runs')
        job = {'id': 'dataset-0-a', 'dataset': task['accuracy']['datasets'][0], 'receipt': {},
               'catalog': {'local_path': '/data/ceval'},
               'subset': {'name': 'a', 'split': 'test', 'source_file': 'test.jsonl', 'metrics': {}}}
        prepared = {'asset': {'path': '/data/prepared', 'selection': {'count': 1, 'records': [{'file': 'test.jsonl', 'row_index': 0}]}}}
        value = request(task, job, prepared, observations['evaluator'], 'http://127.0.0.1:8000', session, False)
        self.assertEqual(value['evaluator']['python'], result['performance_handoff']['benchmark_python'])
        self.assertEqual(value['evaluator']['version'], result['performance_handoff']['evalscope_version'])

    def test_legacy_receipt_rejected_but_sdk_receipt_kept(self):
        from customer_assets.cli import validate
        parent = self.root / 'runs/r/vllm-migration-prepare/customer-assets'
        parent.mkdir(parents=True)
        request = {'operation': 'evalscope_environment', 'install': {'venv': str(self.venv)}}
        old = parent / 'environment-selection-sdk.json'
        old.write_text(json.dumps({'modelscope': '1.0.0'}))
        self.assertEqual(validate(request, parent), 'evalscope_environment')
        for filename, content in (('evalscope-selection.json', {}), ('environment-selection-old.json', {'evalscope': '1.0.0'})):
            path = parent / filename
            path.write_text(json.dumps(content))
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                validate(request, parent)
            path.unlink()


class ContextTests(unittest.TestCase):
    def test_context_optional_and_input_contract_unchanged(self):
        from customer_workflow import context
        from customer_workflow.contract import normalize
        task = normalize(customer())
        self.assertEqual(context.installation({}, task).options['venv'], '/data/evalscope-venv')
        for backend in ('evalscope', -1):
            value = customer()
            value['accuracy']['evaluator'] = backend
            self.assertEqual(normalize(value)['accuracy']['evaluator'], 'evalscope')
        for group in ('environment', 'accuracy'):
            value = customer()
            value[group]['evalscope_install'] = {}
            with self.assertRaises(ValueError):
                normalize(value)

    def test_aisbench_install_settings_rejected_even_empty_or_help(self):
        from customer_workflow.context import installation
        from customer_workflow.contract import normalize
        task = customer()
        task['accuracy']['evaluator'] = 'AISBench'
        task = normalize(task)
        self.assertIsNone(installation({}, task))
        for options in ({}, {'help': True}, {'upgrade': False}):
            with self.assertRaisesRegex(ValueError, '不适用'):
                installation({'evalscope_install': options}, task)

    def test_context_load_and_asset_output_boundaries(self):
        from customer_workflow import context
        from customer_workflow.contract import normalize
        from coordination.runtime.sources import ref
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            task = root / 'task.json'
            task.write_text(json.dumps(customer()))
            value = {'schema_version': '1.0', 'task_source': ref(task), 'observations': {}, 'evidence': [],
                     'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
                     'evalscope_install': {'upgrade': True, 'version': '1.2.3'}}
            path = root / 'context.json'
            path.write_text(json.dumps(value))
            self.assertEqual(context.load(path, ref(task))['evalscope_install'], value['evalscope_install'])
            value['evalscope_install']['upgrade'] = 1
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                context.load(path, ref(task))
            normalized = normalize(customer())
            normalized['model']['path'] = str(root / 'model')
            with self.assertRaises(ValueError):
                context.installation({'evalscope_install': {'venv': str(root / 'model/venv')}}, normalized)
            with self.assertRaises(ValueError):
                context.installation({'evalscope_install': {'venv': '/data/ceval/env'}}, normalized)


class FlowTests(unittest.TestCase):
    def test_every_entry_passes_install_options_and_rejects_changed_version(self):
        from customer_workflow import effects
        from customer_workflow.evaluation import probe
        from customer_workflow.evaluation.selection import Plan
        from evalscope_installation import normalize
        session = Mock(value={'steps': {}})
        result = {**installed(Path('/shared')), 'versions': {'evalscope': '1.2.3'}}
        task = {'accuracy': {'evaluator': 'evalscope'}}
        install = Plan(normalize({'venv': '/shared', 'upgrade': True}))
        with patch.object(effects, 'operation', return_value=result) as operation, patch.object(probe, 'check'):
            effects.evaluator(session, task, Path('/runs'), install)
            effects.evaluator(session, task, Path('/runs'), install)
            self.assertEqual(operation.call_count, 2)
            self.assertEqual(operation.call_args.args[3], {'operation': 'evalscope_environment', 'install': install.options})
            result['versions']['evalscope'] = '2.0.0'
            with self.assertRaisesRegex(ValueError, '新.*run-id'):
                effects.evaluator(session, task, Path('/runs'), install)
            self.assertEqual(session.value['evalscope_environment']['version'], '1.2.3')
            self.assertTrue(session.value['evalscope_environment']['invalidated'])

    def test_preview_stops_all_business_steps(self):
        from customer_workflow import runner
        for flag in ('help', 'dry_run'):
            session = Mock(value={'steps': {}})
            session.finish.return_value = (0, {'status': 'not_run'})
            with patch.object(runner.effects, 'evaluator', return_value={'status': 'not_run'}), \
                    patch.object(runner, 'discover') as discover, patch.object(runner, 'prepare') as prepare:
                from customer_workflow.contract import normalize
                code, result = runner.workflow(session, normalize(customer()),
                    {'observations': {}, 'evalscope_install': {flag: True}}, Path('/runs'),
                    runner.context.installation({'evalscope_install': {flag: True}}, normalize(customer())))
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'not_run')
            discover.assert_not_called()
            prepare.assert_not_called()

    def test_binding_persists_across_real_session_resume_and_old_receipts_rejected(self):
        from customer_workflow.state import Session
        from customer_workflow.evaluation.lifecycle import bind_environment
        from customer_workflow.context import installation
        from customer_workflow.contract import normalize
        task = normalize(customer())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps(customer()))
            session = Session(root / 'runs', source, task, 'r')
            result = {**installed(root / 'shared'), 'method': 'install_evalscope', 'versions': {'evalscope': '1.2.3'}}
            receipt = session.artifact('environment-result', {'result': result})
            session.step('environment', 'completed', receipt=receipt)
            bind_environment(session, result)
            resumed = Session(root / 'runs', source, task, 'r', session.path)
            self.assertEqual(resumed.value['evalscope_environment'], session.value['evalscope_environment'])
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                bind_environment(resumed, {**result, 'python': '/different/python'})
            blocked = Session(root / 'runs', source, task, 'r', resumed.path)
            self.assertIn('新 run-id', installation({}, task, blocked.value).problem)
            old = session.artifact('legacy-result', {'result': {'selection': {'path': '/old'}}})
            session.step('environment', 'completed', receipt=old)
            legacy = Session(root / 'runs', source, task, 'r', session.path)
            self.assertIn('新 run-id', installation({}, task, legacy.value).problem)

    def test_failed_resume_does_not_execute_or_export_old_precision(self):
        from customer_workflow import runner
        from customer_workflow.contract import normalize
        session = Mock(value={'steps': {}})
        session.ready.side_effect = lambda key: None if key == 'environment' else {'old': True}
        session.finish.return_value = (3, {'status': 'blocked'})
        prepared = {'status': 'completed'}
        with patch.object(runner, 'discover'), patch.object(runner, 'prepare', return_value=prepared), \
                patch.object(runner.recovery, 'freeze'), patch.object(runner, 'data_jobs', return_value=[]), \
                patch.object(runner, 'migrate'), patch.object(runner, 'precision') as precision, \
                patch.object(runner, 'deliver') as deliver:
            observations = {key: {} for key in ('image_request', 'target', 'version_review', 'model_resolution')}
            result = runner.workflow(session, normalize(customer()), {'observations': observations}, Path('/runs'),
                                     runner.context.installation({}, normalize(customer())))
        self.assertEqual(result[0], 3)
        precision.assert_not_called()
        deliver.assert_not_called()


if __name__ == '__main__':
    unittest.main()
