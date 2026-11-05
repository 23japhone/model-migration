"""客户输入是独立契约，不放宽原阶段请求。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'skills/vllm-model-migration/scripts'
sys.path.insert(0, str(SCRIPTS))


def customer():
    return {'model': {'model_repository_id': 'org/model', 'path': -1, 'model_name': 'my-model'},
            'environment': {'image_name': -1, 'container_name': -1, 'available_npu_devices': [0, 1, 2, 3]},
            'accuracy': {'evaluator': -1, 'datasets': [
                {'name': 'ceval', 'path': '/data/ceval', 'subset_list': -1, 'limit': -1}]}}


class CustomerContractTests(unittest.TestCase):
    def test_defaults_keep_unresolved_effects_explicit(self):
        from customer_workflow.contract import normalize
        raw = customer()
        value = normalize(raw)
        self.assertEqual(value['environment']['container_name'], 'vllm_my-model')
        self.assertEqual(set(value), {'model', 'environment', 'accuracy'})
        self.assertEqual(value['accuracy']['evaluator'], 'evalscope')
        self.assertIsNone(value['accuracy']['datasets'][0]['limit'])
        self.assertEqual(value['model']['path'], -1)
        self.assertEqual(raw, customer())

    def test_only_supported_unspecified_forms_are_default(self):
        from customer_workflow.contract import normalize
        paths = [('model', 'path'), ('model', 'model_repository_id'),
                 ('environment', 'image_name'), ('environment', 'container_name'),
                 ('environment', 'available_npu_devices'), ('accuracy', 'evaluator')]
        for group, key in paths:
            for invalid in ('', '-1', True, -1.0):
                value = customer()
                value[group][key] = invalid
                with self.subTest(field=key, value=invalid), self.assertRaises(ValueError):
                    normalize(value)
            value = customer()
            del value[group][key]
            missing = normalize(value)
            value[group][key] = None
            self.assertEqual(normalize(value), missing)
            value[group][key] = -1
            self.assertEqual(normalize(value), missing)

    def test_explicit_devices_subsets_and_per_subset_limit(self):
        from customer_workflow.contract import normalize
        raw = customer()
        raw['environment']['available_npu_devices'] = [1, 3]
        raw['accuracy']['datasets'][0].update(subset_list=['a', 'b'], limit=10)
        self.assertEqual(normalize(raw)['environment']['available_npu_devices'], [1, 3])
        for value in ('0,1', [], [0, 0], [True], [-1]):
            bad = copy.deepcopy(raw)
            bad['environment']['available_npu_devices'] = value
            with self.assertRaises(ValueError):
                normalize(bad)
        for value in ('a,b', [], ['a', 'a'], [1]):
            bad = copy.deepcopy(raw)
            bad['accuracy']['datasets'][0]['subset_list'] = value
            with self.assertRaises(ValueError):
                normalize(bad)

    def test_old_customer_fields_are_rejected(self):
        from customer_workflow.contract import normalize
        for group, key in (('model', 'served_model_name'), ('environment', 'vllm_ascend_image'),
                           ('environment', 'evaluator_python'), ('environment', 'host'),
                           ('environment', 'port'), ('environment', 'npu_devices')):
            raw = customer()
            raw[group][key] = -1
            with self.subTest(field=key), self.assertRaises(ValueError):
                normalize(raw)
        raw = customer()
        raw['deployment'] = {'container_name': -1, 'host': -1, 'port': -1, 'npu_devices': -1}
        with self.assertRaises(ValueError):
            normalize(raw)

    def test_template_has_exact_three_group_shape(self):
        from customer_workflow.contract import normalize
        raw = json.loads((SCRIPTS.parent / 'assets/template.json').read_text())
        self.assertEqual(set(raw), {'model', 'environment', 'accuracy'})
        self.assertEqual(set(raw['model']), {'model_repository_id', 'path', 'model_name'})
        self.assertEqual(set(raw['environment']), {'image_name', 'container_name', 'available_npu_devices'})
        self.assertEqual(set(raw['accuracy']), {'evaluator', 'datasets'})
        for row in raw['accuracy']['datasets']:
            self.assertEqual(set(row), {'name', 'path', 'subset_list', 'limit'})
        self.assertEqual(normalize(raw), raw)

    def test_aisbench_and_unique_datasets(self):
        from customer_workflow.contract import normalize
        raw = customer()
        raw['accuracy']['evaluator'] = 'aisbench'
        self.assertEqual(normalize(raw)['accuracy']['evaluator'], 'aisbench')
        raw['accuracy']['datasets'].append(copy.deepcopy(raw['accuracy']['datasets'][0]))
        with self.assertRaises(ValueError):
            normalize(raw)

    def test_public_customer_plan_has_no_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / 'task.json'
            path.write_text(json.dumps(customer()))
            run = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'migrate.py'), '--task', str(path),
                                  '--work-root', str(root / 'runs'), '--run-id', 'customer', '--plan'],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse((root / 'runs').exists())
            result = json.loads(run.stdout)
            self.assertEqual(result['status'], 'not_run')
            self.assertEqual(set(result['deliverables']),
                             {'benchmark_plan.json', 'accuracy_plan.json', 'deployment.json'})


if __name__ == '__main__':
    unittest.main()
