"""EvalScope 续接环境选择与效果前核验；不安装依赖、不访问真实共享 venv。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_customer_contract import customer
from customer_workflow import context, effects, runner
from customer_workflow.contract import normalize
from customer_workflow.state import Session


class ResumeInstallationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.source = self.root / 'task.json'
        self.source.write_text(json.dumps(customer()))
        self.task = normalize(customer())
        self.session = Session(self.root / 'runs', self.source, self.task, 'resume')
        self.venv = self.root / 'shared'
        (self.venv / 'bin').mkdir(parents=True)
        (self.venv / 'bin/python').write_text('synthetic interpreter; never execute')

    def plan(self, options=None):
        supplied = {} if options is None else {'evalscope_install': options}
        return context.installation(supplied, self.task, self.session.value)

    def operation(self, session, key, owner, request, work_root):
        options = request['install']
        if options.get('help') or options.get('dry_run'):
            result = {'status': 'not_run', 'mode': 'help' if options.get('help') else 'dry_run'}
        else:
            version = options.get('version', '1.2.3')
            root = Path(options['venv'])
            result = {'status': 'success', 'method': 'install_evalscope', 'venv': str(root),
                      'python': str(root / 'bin/python'), 'evalscope_executable': str(root / 'bin/evalscope'),
                      'evalscope_version': version, 'versions': {'evalscope': version},
                      'package': 'evalscope[perf]' + ('==' + options['version'] if 'version' in options else '')}
        source = session.artifact(key, request)
        receipt = session.artifact(key + '-result', {'status': 'not_run' if result['status'] == 'not_run' else 'completed',
            'operation': 'evalscope_environment', 'inputs': request, 'result': result})
        session.step(key, 'not_run' if result['status'] == 'not_run' else 'completed', request=source, receipt=receipt)
        return result

    def observe(self, **changes):
        value = {'python': str(self.venv / 'bin/python'), 'prefix': str(self.venv), 'version': '1.2.3', **changes}
        return subprocess.CompletedProcess([], 0, json.dumps(value), '')

    def install(self, options=None):
        from customer_workflow.evaluation import probe
        plan = self.plan(options)
        with patch.object(effects, 'operation', side_effect=self.operation) as operation, \
                patch.object(probe.subprocess, 'run', return_value=self.observe()) as inspect:
            result = effects.evaluator(self.session, self.task, self.root / 'runs', plan)
        return result, operation, inspect

    def first(self, **options):
        return self.install({'venv': str(self.venv), **options})

    def test_omitted_partial_and_flags_inherit_environment_and_pin_actual_version(self):
        self.first(upgrade=True)
        for provided in (None, {}, {'upgrade': True}, {'version': '1.2.3'}):
            with self.subTest(provided=provided):
                result, operation, inspect = self.install(provided)
                options = operation.call_args.args[3]['install']
                self.assertEqual(options['venv'], str(self.venv))
                self.assertEqual(options['python'], sys.executable)
                self.assertEqual(options['version'], '1.2.3')
                self.assertEqual(options.get('upgrade', False), provided == {'upgrade': True})
                self.assertFalse(options.get('help', False))
                self.assertFalse(options.get('dry_run', False))
                self.assertEqual(result['python'], str(self.venv / 'bin/python'))
                operation.assert_called_once()
                inspect.assert_called_once()

    def test_selection_survives_failed_first_attempt(self):
        plan = self.plan({'venv': str(self.venv), 'version': '1.2.3', 'upgrade': True})
        with patch.object(effects, 'operation', side_effect=ValueError('pip failed')):
            with self.assertRaisesRegex(ValueError, 'pip failed'):
                effects.evaluator(self.session, self.task, self.root, plan)
        self.assertNotIn('evalscope_environment', self.session.value)
        resumed = Session(self.root / 'runs', self.source, self.task, 'resume', self.session.path)
        self.assertEqual(resumed.value['evalscope_install_selection'], self.session.value['evalscope_install_selection'])
        self.session = resumed
        self.assertEqual(self.plan().options, {'python': sys.executable, 'venv': str(self.venv), 'version': '1.2.3'})

    def test_explicit_target_changes_do_not_install_or_invalidate_binding(self):
        self.first()
        binding = copy.deepcopy(self.session.value['evalscope_environment'])
        alternate = self.root / 'other-python'
        alternate.write_text('not executed')
        for options in ({'venv': str(self.root / 'other')}, {'version': '2.0.0'}, {'python': str(alternate)}):
            with self.subTest(options=options), patch.object(effects, 'operation') as operation:
                with self.assertRaisesRegex(ValueError, '新 run-id'):
                    effects.evaluator(self.session, self.task, self.root, self.plan(options))
                operation.assert_not_called()
                self.assertEqual(self.session.value['evalscope_environment'], binding)

    def test_first_failed_unpinned_requirement_cannot_change_on_resume(self):
        with patch.object(effects, 'operation', side_effect=ValueError('failed')):
            with self.assertRaises(ValueError):
                effects.evaluator(self.session, self.task, self.root, self.plan({'venv': str(self.venv)}))
        self.assertIsNotNone(self.plan({'version': '1.2.3'}).problem)

    def test_actual_drift_is_rejected_before_install_and_stays_invalidated(self):
        from customer_workflow.evaluation import probe
        self.first()
        with patch.object(probe.subprocess, 'run', return_value=self.observe(version='2.0.0')), \
                patch.object(effects, 'operation') as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        operation.assert_not_called()
        self.assertTrue(self.session.value['evalscope_environment']['invalidated'])
        with patch.object(effects, 'operation') as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                self.install()
        operation.assert_not_called()

    def test_missing_bound_python_invalidates_without_recreation(self):
        self.first()
        (self.venv / 'bin/python').unlink()
        with patch.object(effects, 'operation') as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        operation.assert_not_called()
        self.assertTrue(self.session.value['evalscope_environment']['invalidated'])

    def test_unknown_probe_failures_keep_binding_and_logs(self):
        from customer_workflow.evaluation import probe
        self.first()
        binding = copy.deepcopy(self.session.value['evalscope_environment'])
        for outcome in (subprocess.TimeoutExpired(['python'], 30, output='partial', stderr='timed out'),
                        PermissionError('denied'), subprocess.CompletedProcess([], 1, '', 'error'),
                        subprocess.CompletedProcess([], 0, 'invalid JSON', '')):
            with self.subTest(outcome=outcome):
                with patch.object(probe.subprocess, 'run', side_effect=outcome if isinstance(outcome, Exception) else None,
                                  return_value=outcome) as inspect, patch.object(effects, 'operation') as operation:
                    with self.assertRaises((ValueError, OSError, RuntimeError)):
                        effects.evaluator(self.session, self.task, self.root, self.plan())
                self.assertEqual(inspect.call_args.kwargs['timeout'], 30)
                operation.assert_not_called()
                self.assertEqual(self.session.value['evalscope_environment'], binding)
        self.assertTrue(list(self.session.attempt.rglob('*.process.json')))

    def test_wrong_venv_prefix_invalidates_without_installation(self):
        from customer_workflow.evaluation import probe
        self.first()
        with patch.object(probe.subprocess, 'run', return_value=self.observe(prefix='/elsewhere')), \
                patch.object(effects, 'operation') as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        operation.assert_not_called()
        self.assertTrue(self.session.value['evalscope_environment']['invalidated'])

    def test_post_install_binding_change_still_invalidates(self):
        from customer_workflow.evaluation import probe
        self.first()
        with patch.object(probe.subprocess, 'run', return_value=self.observe()), \
                patch.object(effects, 'operation', return_value={'python': str(self.venv / 'bin/python'),
                                                               'versions': {'evalscope': '2.0.0'}}) as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        operation.assert_called_once()
        self.assertTrue(self.session.value['evalscope_environment']['invalidated'])

    def test_previews_preserve_success_and_selection_and_do_not_probe(self):
        self.first()
        selection = copy.deepcopy(self.session.value['evalscope_install_selection'])
        success = copy.deepcopy(self.session.value['steps']['environment'])
        binding = copy.deepcopy(self.session.value['evalscope_environment'])
        for flag in ('help', 'dry_run'):
            result, operation, inspect = self.install({flag: True, 'venv': str(self.root / 'preview'), 'version': '2.0.0'})
            self.assertEqual(result['status'], 'not_run')
            self.assertIn('新 run-id', result['notice'])
            self.assertEqual(operation.call_args.args[1], 'environment_preview')
            inspect.assert_not_called()
            self.assertEqual(self.session.value['steps']['environment'], success)
            self.assertEqual(self.session.value['evalscope_install_selection'], selection)
            self.assertEqual(self.session.value['evalscope_environment'], binding)
        self.assertEqual(self.plan().options['venv'], str(self.venv))
        self.assertFalse(self.plan().options.get('dry_run'))

    def test_initial_preview_does_not_freeze_future_formal_selection(self):
        result, _, inspect = self.first(help=True, dry_run=True)
        self.assertEqual(result['mode'], 'help')
        self.assertNotIn('evalscope_install_selection', self.session.value)
        self.assertNotIn('evalscope_environment', self.session.value)
        inspect.assert_not_called()
        self.assertEqual(self.plan().options['venv'], '/data/evalscope-venv')

    def test_existing_successful_receipts_recover_selection(self):
        self.first()
        del self.session.value['evalscope_install_selection']
        self.session.save()
        result, operation, _ = self.install()
        self.assertEqual(operation.call_args.args[3]['install']['venv'], str(self.venv))
        self.assertEqual(result['versions']['evalscope'], '1.2.3')
        self.assertIn('evalscope_install_selection', self.session.value)

    def test_missing_history_requires_new_task_instead_of_global_defaults(self):
        self.first()
        del self.session.value['evalscope_install_selection']
        del self.session.value['steps']['environment']['request']
        with patch.object(effects, 'operation') as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        operation.assert_not_called()

    def test_no_context_resume_keeps_selection(self):
        self.first()
        middle = Session(self.root / 'runs', self.source, self.task, 'resume', self.session.path)
        middle.finish('blocked', next_action='context_required')
        again = Session(self.root / 'runs', self.source, self.task, 'resume', middle.path)
        self.assertEqual(again.value['evalscope_install_selection'], self.session.value['evalscope_install_selection'])

    def test_inherited_path_is_used_before_global_default_path_guard(self):
        self.first()
        args = SimpleNamespace(context=self.root / 'context.json', work_root=Path('/data'))
        args.context.write_text('{}')
        supplied = {'task_source': self.session.source}
        plan = context.preflight(supplied, self.task, args, self.root / 'new-runs', self.session.value)
        self.assertEqual(plan.options['venv'], str(self.venv))

    def test_environment_rejection_keeps_independent_assets(self):
        self.first()
        plan = self.plan({'version': '2.0.0'})
        with patch.object(effects, 'operation') as operation, patch.object(runner, 'prepare') as prepare, \
                patch.object(runner, 'data_jobs', return_value=[]) as data, patch.object(runner, 'precision') as precision:
            prepare.return_value = None
            code, result = runner.workflow(self.session, self.task, {'observations': {'model_resolution': {}}}, self.root, plan)
        self.assertEqual(code, 3)
        self.assertEqual(result['steps']['environment']['status'], 'blocked')
        operation.assert_not_called()
        prepare.assert_called_once()
        data.assert_called_once()
        precision.assert_not_called()

    def test_install_failure_cannot_hide_environment_drift(self):
        from customer_workflow.evaluation import probe
        self.first()
        with patch.object(probe.subprocess, 'run', side_effect=[self.observe(), self.observe(version='2.0.0')]), \
                patch.object(effects, 'operation', side_effect=ValueError('installer rejected changed version')):
            with self.assertRaises(ValueError):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        self.assertTrue(self.session.value['evalscope_environment'].get('invalidated'))

    def test_inherited_venv_symlink_redirection_is_actual_drift(self):
        self.first()
        moved = self.root / 'moved-environment'
        self.venv.rename(moved)
        self.venv.symlink_to(moved, target_is_directory=True)
        with patch.object(effects, 'operation') as operation:
            with self.assertRaises(ValueError):
                effects.evaluator(self.session, self.task, self.root, self.plan())
        operation.assert_not_called()
        self.assertTrue(self.session.value['evalscope_environment'].get('invalidated'))

    def test_unrecoverable_history_does_not_turn_preview_into_business(self):
        self.first()
        del self.session.value['evalscope_install_selection']
        del self.session.value['steps']['environment']['request']
        for flag in ('help', 'dry_run'):
            with self.subTest(flag=flag), patch.object(runner, 'discover') as discover, \
                    patch.object(effects, 'operation') as operation:
                with self.assertRaisesRegex(ValueError, '新 run-id'):
                    runner.workflow(self.session, self.task, {'observations': {}}, self.root,
                                    self.plan({flag: True}))
                discover.assert_not_called()
                operation.assert_not_called()

    def test_changed_unavailable_base_python_still_requires_new_task(self):
        self.first()
        with patch.object(effects, 'operation') as operation:
            with self.assertRaisesRegex(ValueError, '新 run-id'):
                effects.evaluator(self.session, self.task, self.root,
                                  self.plan({'python': str(self.root / 'missing-python')}))
        operation.assert_not_called()
        self.assertNotIn('invalidated', self.session.value['evalscope_environment'])

    def test_historical_selection_requires_consistent_request_and_result(self):
        from coordination.runtime import sources
        self.first()
        del self.session.value['evalscope_install_selection']
        original = copy.deepcopy(self.session.value['steps']['environment'])
        for kind in ('command', 'version', 'request'):
            with self.subTest(kind=kind):
                row = copy.deepcopy(original)
                request = sources.bound(row['request'])
                receipt = sources.bound(row['receipt'])
                if kind == 'command':
                    request['install']['python'] = 'python3'
                    receipt['inputs'] = copy.deepcopy(request)
                elif kind == 'version':
                    receipt['result']['versions']['evalscope'] = '2.0.0'
                else:
                    receipt['inputs']['install']['venv'] = '/different'
                row['request'] = self.session.artifact('historical-request', request)
                row['receipt'] = self.session.artifact('historical-result', receipt)
                self.session.value['steps']['environment'] = row
                with patch.object(effects, 'operation') as operation:
                    with self.assertRaisesRegex(ValueError, '新 run-id'):
                        effects.evaluator(self.session, self.task, self.root, self.plan())
                operation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
