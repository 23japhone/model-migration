"""客户缺省输入兼容；公开计划始终无外部效果。"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_customer_contract import customer, SCRIPTS


class DefaultContractTests(unittest.TestCase):
    def test_missing_null_and_integer_sentinel_are_equivalent(self):
        from customer_workflow.contract import normalize
        empty = {'model': {}, 'environment': {}, 'accuracy': {}}
        expected = normalize(empty)
        self.assertEqual(expected['model']['model_name'], -1)
        self.assertEqual(expected['environment']['available_npu_devices'], -1)
        self.assertEqual(expected['accuracy'], {'evaluator': 'evalscope', 'datasets': [
            {'name': name, 'path': '/data/' + name, 'subset_list': -1, 'limit': None}
            for name in ('ceval', 'gsm8k')]})
        from customer_workflow.contract import FIELDS
        for marker in (None, -1):
            value = {group: dict.fromkeys(keys, marker) for group, keys in FIELDS.items()}
            self.assertEqual(normalize(value), expected)
        self.assertEqual(empty, {'model': {}, 'environment': {}, 'accuracy': {}})

    def test_each_optional_field_normalizes_without_rewriting_raw(self):
        from customer_workflow.contract import FIELDS, normalize
        for group, keys in FIELDS.items():
            for key in keys:
                raw = customer()
                raw[group][key] = -1
                expected = normalize(raw)
                for missing in (True, False):
                    changed = copy.deepcopy(raw)
                    if missing:
                        del changed[group][key]
                    else:
                        changed[group][key] = None
                    before = copy.deepcopy(changed)
                    self.assertEqual(normalize(changed), expected, (group, key, missing))
                    self.assertEqual(changed, before)

    def test_explicit_dataset_selection_and_leaf_defaults(self):
        from customer_workflow.contract import normalize
        raw = customer()
        raw['accuracy']['datasets'] = [{'name': 'gsm8k'}]
        row = normalize(raw)['accuracy']['datasets']
        self.assertEqual(row, [{'name': 'gsm8k', 'path': '/data/gsm8k', 'subset_list': -1, 'limit': None}])
        for marker in (None, -1):
            raw['accuracy']['datasets'] = [{'name': 'gsm8k', 'path': marker, 'subset_list': marker, 'limit': marker}]
            self.assertEqual(normalize(raw)['accuracy']['datasets'], row)
        for bad in ([{}], [{'name': None}], [{'name': -1}], [{'name': 'custom'}], []):
            raw['accuracy']['datasets'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize(raw)

    def test_invalid_values_are_not_unspecified(self):
        from customer_workflow.contract import FIELDS, normalize
        for group, keys in FIELDS.items():
            for key in keys:
                invalid_list = [{}] if key == 'gpu_baselines' else []
                for bad in ('', '  ', '-1', -1.0, True, False, invalid_list, {}):
                    raw = customer()
                    raw[group][key] = bad
                    with self.subTest(field=key, bad=bad), self.assertRaises(ValueError):
                        normalize(raw)
        for raw in ({}, {'model': {}, 'environment': {}, 'accuracy': None}):
            with self.assertRaises(ValueError):
                normalize(raw)

    def test_container_names_are_stable_valid_and_collision_resistant(self):
        from customer_workflow.contract import normalize
        seen = set()
        for name in ('org/model', 'org model', '模型', 'x' * 200):
            raw = customer()
            raw['model']['model_name'] = name
            value = normalize(raw)['environment']['container_name']
            self.assertRegex(value, r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$')
            self.assertTrue(value.endswith(hashlib.sha256(name.encode()).hexdigest()[:12]))
            self.assertEqual(value, normalize(raw)['environment']['container_name'])
            self.assertNotIn(value, seen)
            seen.add(value)
        raw['environment']['container_name'] = 'explicit-name'
        self.assertEqual(normalize(raw)['environment']['container_name'], 'explicit-name')
        raw['environment']['container_name'] = 'invalid/name'
        with self.assertRaises(ValueError):
            normalize(raw)

    def test_prompt_fills_missing_and_null_before_derived_defaults(self):
        from customer_workflow.intent import resolved
        from customer_workflow.contract import normalize
        raw = customer()
        raw['model']['model_name'] = 'org/model'
        for marker in (None, -1):
            raw['environment']['container_name'] = marker
            result = normalize(resolved(raw, {'environment.container_name': 'chosen'}))
            self.assertEqual(result['environment']['container_name'], 'chosen')
        del raw['environment']['container_name']
        self.assertEqual(normalize(resolved(raw, {'environment.container_name': 'chosen'}))['environment']['container_name'], 'chosen')

    def test_all_default_public_plan_does_not_read_assets_or_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps({'model': {}, 'environment': {}, 'accuracy': {}}))
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'migrate.py'), '--task', str(source),
                '--context', str(root / 'nonexistent-context'), '--work-root', str(root / 'runs'),
                '--run-id', 'defaults', '--plan'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value['status'], 'not_run')
            self.assertIn('model.model_name', ' '.join(value['uncovered']))
            self.assertFalse((root / 'runs').exists())

    def test_final_aisbench_backend_is_used_before_environment_checks(self):
        from coordination.runtime.sources import ref
        from customer_workflow import context
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps(customer()))
            path = root / 'context.json'
            path.write_text(json.dumps({'schema_version': '1.0', 'task_source': ref(source),
                'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
                'observations': {}, 'evidence': [], 'prompt_fields': {'accuracy.evaluator': 'aisbench'}}))
            args = SimpleNamespace(context=path, work_root=Path('/data'), run_id='r', resume=None)
            with patch('customer_workflow.state.Session') as session:
                context.start(args, source, customer(), root / 'runs')
            session.assert_called_once()

    def test_prompt_asset_boundary_checked_before_manifest_creation(self):
        from coordination.runtime.sources import ref
        from customer_workflow import context
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps(customer()))
            path = root / 'context.json'
            parent = root / 'weights/run'
            path.write_text(json.dumps({'schema_version': '1.0', 'task_source': ref(source),
                'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
                'observations': {}, 'evidence': [], 'prompt_fields': {
                    'accuracy.evaluator': 'aisbench', 'model.path': str(root / 'weights')}}))
            args = SimpleNamespace(context=path, work_root=root / 'weights', run_id='r', resume=None)
            with self.assertRaises(ValueError):
                context.start(args, source, customer(), parent)
            self.assertFalse(parent.exists())

    def test_resume_uses_frozen_effective_backend(self):
        from customer_workflow.state import Session
        from customer_workflow.contract import normalize
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps(customer()))
            raw = normalize(customer())
            first = Session(root / 'runs', source, raw, 'r')
            effective = copy.deepcopy(raw)
            effective['accuracy']['evaluator'] = 'aisbench'
            first.value['declared_inputs'] = effective
            receipt = first.artifact('environment-result', {'result': {'versions': {'ais-bench-benchmark': '1.0.0'}}})
            first.step('environment', 'completed', receipt=receipt)
            again = Session(root / 'runs', source, raw, 'r', first.path)
            self.assertEqual(again.previous['declared_inputs']['accuracy']['evaluator'], 'aisbench')

    def test_replaced_default_dataset_path_does_not_guard_unused_location(self):
        from coordination.runtime.sources import ref
        from customer_workflow import context
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            raw = customer()
            del raw['accuracy']['datasets']
            source = root / 'task.json'
            source.write_text(json.dumps(raw))
            path = root / 'context.json'
            path.write_text(json.dumps({'schema_version': '1.0', 'task_source': ref(source),
                'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
                'observations': {}, 'evidence': [], 'prompt_fields': {'accuracy.evaluator': 'aisbench',
                    'accuracy.datasets': [{'name': 'ceval', 'path': str(root / 'actual-data')}]}}))
            args = SimpleNamespace(context=path, work_root=Path('/data/ceval/runs'), run_id='r', resume=None)
            # 只验证路径判定；绝不向真实 /data 写入台账。
            with patch('pathlib.Path.mkdir'), patch('customer_workflow.state.Session.save'):
                _, session, _, _, _ = context.start(args, source, raw, args.work_root / 'r/customer-workflow')
            self.assertEqual(session.value['inputs']['accuracy']['datasets'][0]['path'], '/data/ceval')


if __name__ == '__main__':
    unittest.main()
