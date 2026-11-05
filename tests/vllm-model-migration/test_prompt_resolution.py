"""公开客户入口只合成一次；原始快照不参与实际分支选择。"""
import contextlib
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from test_customer_contract import customer
from coordination.runtime.sources import ref
from customer_workflow import cli, context, contract, intent, runner


class PromptResolutionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.source = self.root / 'task.json'
        self.context = self.root / 'context.json'
        self.args = SimpleNamespace(task=self.source, context=self.context, work_root=self.root / 'runs',
                                    run_id='prompt', resume=None, action='run', plan=False)

    def invoke(self, raw, prompt, **extra):
        self.source.write_text(json.dumps(raw), encoding='utf-8')
        supplied = {'schema_version': '1.0', 'task_source': ref(self.source),
                    'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
                    'observations': {}, 'evidence': [], 'prompt_fields': prompt, **extra}
        self.context.write_text(json.dumps(supplied), encoding='utf-8')
        output = io.StringIO()
        with patch.object(runner, 'workflow', side_effect=lambda session, *_: session.finish('not_run')) as workflow, \
                patch.object(context, 'preflight', wraps=context.preflight) as preflight, \
                patch.object(context, 'installation', wraps=context.installation) as installation, \
                patch.object(intent, 'compose', wraps=intent.compose) as resolved, \
                contextlib.redirect_stdout(output):
            code = cli.main(self.args)
        return code, json.loads(output.getvalue()), workflow, preflight, installation, resolved

    def test_execution_composes_once_and_shares_preflight_task(self):
        for marker in ('missing', None, -1):
            with self.subTest(marker=marker):
                raw = customer()
                raw['model']['model_name'] = 'org/model'
                if marker == 'missing':
                    del raw['environment']['container_name']
                else:
                    raw['environment']['container_name'] = marker
                code, result, workflow, preflight, _, resolved = self.invoke(raw, {
                    'environment.container_name': 'vllm_model', 'accuracy.evaluator': 'aisbench'})
                self.assertEqual(code, 0, result)
                task = workflow.call_args.args[1]
                self.assertEqual(task['environment']['container_name'], 'vllm_model')
                self.assertEqual(task['model']['model_name'], 'org/model')
                resolved.assert_called_once()
                self.assertIs(preflight.call_args.args[1], task)
                self.assertEqual(result['inputs'], contract.normalize(raw))
                self.assertEqual(result['declared_inputs'], task)
                self.assertEqual(json.loads(self.source.read_text()), raw)

    def test_context_is_loaded_before_any_derived_default(self):
        raw = customer()
        raw['model']['model_name'] = None
        events = []
        load, derive = context.load, contract.container_name

        def loading(*args):
            events.append('context')
            return load(*args)

        def deriving(name):
            events.append(('container', name))
            return derive(name)

        with patch.object(context, 'load', side_effect=loading), \
                patch.object(contract, 'container_name', side_effect=deriving):
            code, result, workflow, *_ = self.invoke(raw, {'model.model_name': 'new/model'})
        self.assertEqual(code, 0, result)
        self.assertEqual(events[:2], ['context', ('container', 'new/model')])
        self.assertEqual(workflow.call_args.args[1]['environment']['container_name'], derive('new/model'))

    def test_effective_backend_controls_installation_guards(self):
        raw = customer()
        with patch.object(context, 'protect', wraps=context.protect) as guard:
            code, result, workflow, _, install, _ = self.invoke(raw, {'accuracy.evaluator': 'aisbench'})
        self.assertEqual(code, 0, result)
        guard.assert_not_called()
        self.assertIs(install.call_args.args[1], workflow.call_args.args[1])
        code, result, workflow, *_ = self.invoke(raw, {'accuracy.evaluator': 'aisbench'}, evalscope_install={})
        self.assertEqual(code, 2, result)
        self.assertIn('不适用', result['reason'])
        workflow.assert_not_called()

    def test_evalscope_overlap_is_rejected_before_manifest_creation(self):
        code, result, workflow, *_ = self.invoke(customer(), {},
            evalscope_install={'venv': str(self.args.work_root / 'venv')})
        self.assertEqual(code, 2, result)
        self.assertIn('重叠', result['reason'])
        self.assertFalse(self.args.work_root.exists())
        workflow.assert_not_called()

    def test_invalid_raw_value_cannot_be_repaired_by_prompt(self):
        raw = customer()
        raw['environment']['container_name'] = 'bad/name'
        with patch.object(context, 'load', wraps=context.load) as load:
            code, result, workflow, *_ = self.invoke(raw, {'environment.container_name': 'valid'})
        self.assertEqual(code, 2, result)
        load.assert_not_called()
        workflow.assert_not_called()
        self.assertFalse(self.args.work_root.exists())

    def test_invalid_prompt_is_not_a_user_value_conflict(self):
        for index, prompt in enumerate(([], {'model.model_name': []}, {'environment.container_name': 'bad/name'},
                                       {'model.served_model_name': 'old'}, {'accuracy.datasets.0.limit': 1})):
            with self.subTest(prompt=prompt):
                self.args.work_root = self.root / ('runs-' + str(index))
                code, result, workflow, *_ = self.invoke(customer(), prompt)
                self.assertEqual(code, 2, result)
                workflow.assert_not_called()
                self.assertFalse(self.args.work_root.exists())

    def test_valid_explicit_conflicts_stop_before_environment_and_business(self):
        raw = customer()
        raw['accuracy']['evaluator'] = 'evalscope'
        code, result, workflow, _, install, resolved = self.invoke(raw, {'accuracy.evaluator': 'aisbench'})
        self.assertEqual(code, 3, result)
        self.assertEqual(result['next_action'], {'owner': 'user', 'conflicts': ['accuracy.evaluator']})
        workflow.assert_not_called()
        install.assert_not_called()
        resolved.assert_called_once()

    def test_explicit_validation_keeps_unresolved_values_without_defaults(self):
        raw = {'model': {'model_name': None}, 'environment': {}, 'accuracy': {'evaluator': -1}}
        original = copy.deepcopy(raw)
        with patch.object(contract, 'container_name', side_effect=AssertionError('no derived defaults')):
            checked = contract.validate(raw)
        self.assertEqual(checked, {group: dict.fromkeys(keys, -1) for group, keys in contract.FIELDS.items()})
        self.assertEqual(raw, original)

    def test_prompt_asset_paths_are_guarded_before_manifest_creation(self):
        self.args.work_root = self.root / 'model/runs'
        code, result, workflow, *_ = self.invoke(customer(), {'model.path': str(self.root / 'model')})
        self.assertEqual(code, 2, result)
        workflow.assert_not_called()
        self.assertFalse(self.args.work_root.exists())

    def test_input_changes_after_preflight_cannot_rebind_old_parameters(self):
        preflight = context.preflight

        def changed(*args):
            preflight(*args)
            raw = customer()
            raw['model']['model_name'] = 'changed-on-disk'
            self.source.write_text(json.dumps(raw))

        with patch.object(context, 'preflight', side_effect=changed):
            code, result, workflow, *_ = self.invoke(customer(), {})
        self.assertEqual(code, 2, result)
        workflow.assert_not_called()
        self.assertFalse(self.args.work_root.exists())

    def test_resume_keeps_snapshot_backend_and_frozen_prompt_distinct(self):
        raw = customer()
        prompt = {'accuracy.evaluator': 'aisbench', 'environment.container_name': 'selected'}
        code, first, workflow, *_ = self.invoke(raw, prompt)
        self.assertEqual(code, 0, first)
        session = workflow.call_args.args[0]
        receipt = session.artifact('aisbench', {'result': {'versions': {'ais-bench-benchmark': '1.0'}}})
        session.step('environment', 'completed', receipt=receipt)
        self.args.resume = session.path
        code, again, _, _, _, resolved = self.invoke(raw, prompt)
        self.assertEqual(code, 0, again)
        self.assertEqual(first['inputs'], again['inputs'])
        self.assertEqual(again['inputs']['accuracy']['evaluator'], 'evalscope')
        self.assertEqual(again['declared_inputs']['accuracy']['evaluator'], 'aisbench')
        resolved.assert_called_once()
        code, rejected, workflow, *_ = self.invoke(raw, {**prompt, 'environment.container_name': 'changed'})
        self.assertEqual(code, 2, rejected)
        self.assertIn('冻结', rejected['reason'])
        workflow.assert_not_called()

    def test_plan_is_json_preview_without_context_or_effects(self):
        self.args.plan = True
        with patch.object(context, 'load', side_effect=AssertionError('plan must not load context')):
            code, result, workflow, _, _, resolved = self.invoke(customer(), {'accuracy.evaluator': 'aisbench'})
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'not_run')
        self.assertEqual(result['inputs']['accuracy']['evaluator'], 'evalscope')
        self.assertIn('尚未合成 prompt', ' '.join(result['uncovered']))
        self.assertFalse(self.args.work_root.exists())
        workflow.assert_not_called()
        resolved.assert_not_called()


if __name__ == '__main__':
    unittest.main()
