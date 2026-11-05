"""客户输入边界与独立资产失败隔离；所有外部效果使用替身。"""
import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_customer_contract import customer

ROOT = Path(__file__).resolve().parents[2]
PREPARE = ROOT / 'skills/vllm-model-migration/skills/vllm-migration-prepare/scripts'
sys.path.insert(0, str(PREPARE))


class InputTests(unittest.TestCase):
    def test_old_evaluator_python_is_rejected_for_every_evaluator(self):
        from customer_workflow.contract import normalize
        for evaluator in ('evalscope', 'aisbench'):
            for python in (-1, '/opt/eval/bin/python'):
                task = customer()
                task['accuracy']['evaluator'] = evaluator
                task['environment']['evaluator_python'] = python
                with self.subTest(evaluator=evaluator, python=python), self.assertRaisesRegex(ValueError, '未知字段'):
                    normalize(task)

    def test_prompt_cannot_restore_removed_environment_fields(self):
        from customer_workflow.intent import resolved
        for field in ('environment.evaluator_python', 'environment.vllm_ascend_image', 'deployment.port'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '未知字段'):
                resolved(customer(), {field: '/opt/eval/bin/python'})

    def test_old_environment_manifest_is_rejected(self):
        from customer_workflow.contract import normalize
        from customer_workflow.state import load_previous
        task = normalize(customer())
        old = json.loads(json.dumps(task))
        old['environment'] = {'vllm_ascend_image': -1, 'evaluator_python': -1}
        session = Mock(previous={'task_id': 'vllm-model-migration-customer', 'run_id': 'run',
                                 'task_source': 'source', 'inputs': old, 'steps': {}}, source='source')
        with patch('customer_workflow.state.sources.bound', return_value=session.previous), \
                self.assertRaisesRegex(ValueError, '同一客户输入'):
            load_previous({'path': '/previous', 'sha256': '0' * 64}, session.source, task, 'run')

class FailureEvidenceTests(unittest.TestCase):
    def test_only_aisbench_task_environment_supplies_asset_interpreter(self):
        from customer_workflow import runner
        session = Mock()
        aisbench = {'python': '/task/bin/python', 'versions': {'ais-bench-benchmark': '1.2.3'}}
        observations = {'model_resolution': {'request': {'source': 'modelscope'}}}
        with patch.object(runner.effects, 'evaluator', return_value=aisbench):
            runner.discover(session, {'accuracy': {'evaluator': 'aisbench'}}, observations, Path('/runs'))
        self.assertEqual(observations['asset_python'], '/task/bin/python')
        modern = {'python': '/borrowed/bin/python', 'versions': {'evalscope': '1.2.3'},
                  'method': 'install_evalscope'}
        observations = {'model_resolution': {'request': {'source': 'local'}}}
        with patch.object(runner.effects, 'evaluator', return_value=modern):
            runner.discover(session, {'accuracy': {'evaluator': 'evalscope'}}, observations, Path('/runs'))
        self.assertNotIn('asset_python', observations)

    def test_sdk_is_prepared_separately_only_after_resolver_reports_missing(self):
        from customer_workflow import effects
        session = Mock()
        session.ready.return_value = None
        failure = effects.OperationBlocked({'reason': 'missing sdk', 'missing_dependency': 'modelscope'})
        with patch.object(effects, 'operation', side_effect=[failure, {'python': '/asset/python'}, {'request': {'source': 'modelscope'}}]) as op:
            result = effects.resolve_model(session, customer(), {}, Path('/runs'))
        self.assertEqual(result['asset_python'], '/asset/python')
        self.assertEqual(op.call_args_list[1].args[3]['packages'], ['modelscope'])
        self.assertEqual(op.call_args_list[2].kwargs['python'], '/asset/python')

    def test_cli_timeout_publishes_blocked_resolution(self):
        from customer_assets import cli
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = root / 'request.json'
            request.write_text(json.dumps({'operation': 'environment', 'versions': {'ais-bench-benchmark': '1.2.3'}}))
            argv = ['customer.py', '--request', str(request), '--work-root', str(root / 'runs'), '--run-id', 'r']
            with patch.object(sys, 'argv', argv), patch.object(cli, 'perform', side_effect=subprocess.TimeoutExpired(['pip'], 1)), \
                    patch('sys.stdout', new_callable=io.StringIO) as out:
                code = cli.main()
            value = json.loads(out.getvalue())
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(Path(value['manifest']).read_text())['status'], 'blocked')

    def test_install_timeout_keeps_partial_logs_and_process(self):
        from customer_assets.environment import command
        with tempfile.TemporaryDirectory() as folder:
            attempt = Path(folder)
            error = subprocess.TimeoutExpired(['python', '-m', 'pip'], 3, output='partial stdout', stderr='partial stderr')
            with patch('customer_assets.environment.call', side_effect=error), self.assertRaises(subprocess.TimeoutExpired):
                command(error.cmd, attempt, 'pip-install', 3)
            self.assertEqual((attempt / 'pip-install.stdout.log').read_text(), 'partial stdout')
            self.assertEqual((attempt / 'pip-install.stderr.log').read_text(), 'partial stderr')
            self.assertTrue(json.loads((attempt / 'pip-install.process.json').read_text())['timed_out'])

    def test_environment_failure_does_not_skip_local_model_resolution(self):
        from customer_workflow import runner
        from customer_workflow.contract import normalize
        session = Mock()
        session.value = {'resolved_observations': {}}
        session.ready.return_value = None
        session.finish.return_value = (3, {})
        supplied = {'observations': {'asset_python': '/asset/python'}}
        with patch.object(runner.effects, 'evaluator', side_effect=ValueError('blocked')), \
                patch.object(runner.effects, 'resolve_model', return_value={'request': {}}) as resolve, \
                patch.object(runner, 'prepare', return_value=None), patch.object(runner, 'data_jobs', return_value=[]), \
                patch.object(runner, 'migrate'), patch.object(runner, 'precision'), patch.object(runner, 'complete', return_value=None):
            runner.workflow(session, normalize(customer()), supplied, Path('/runs'),
                            runner.context.installation(supplied, normalize(customer())))
        resolve.assert_called_once()


if __name__ == '__main__':
    unittest.main()
