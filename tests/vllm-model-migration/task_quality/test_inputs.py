import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from customer_workflow.contract import normalize


def raw():
    return {'model': {}, 'environment': {}, 'accuracy': {'gpu_baselines': [
        {'evaluator': 'evalscope', 'dataset': 'ceval', 'scores': [
            {'metric': 'accuracy', 'gpu_score': 0, 'target': {'max_drop': 0}}]}]}}


class QualityContractTests(unittest.TestCase):
    def test_preserves_missing_baseline_shape(self):
        for sentinel in (-1, None, []):
            task = raw()
            task['accuracy']['gpu_baselines'] = sentinel
            self.assertNotIn('gpu_baselines', normalize(task)['accuracy'])
        old = json.loads((ROOT / 'assets/template.json').read_text())
        self.assertEqual(old, normalize(old))

    def test_optional_fields_and_zero_values(self):
        original = raw()
        baseline = normalize(original)['accuracy']['gpu_baselines'][0]
        self.assertEqual(baseline['scores'][0]['target'], {'max_drop': 0})
        self.assertEqual(baseline['scores'][0]['gpu_score'], 0)
        for key in ('subset_list', 'evaluator_version'):
            for missing in (None, -1):
                task = copy.deepcopy(original)
                task['accuracy']['gpu_baselines'][0][key] = missing
                self.assertEqual(normalize(task)['accuracy']['gpu_baselines'][0], baseline)
        self.assertEqual(original, raw())

    def test_invalid_inputs(self):
        bad = [{'evaluator_version': 'latest'}, {'subset_list': []}, {'prompt_config': {'enable_thinking': 1}},
               {'generation_config': {'max_tokens': True}}, {'scores': []},
               {'scores': [{'metric': 'accuracy', 'gpu_score': True}]},
               {'scores': [{'metric': 'accuracy', 'gpu_score': 1, 'target': {'max_drop': -0.1}}]},
               {'scores': [{'metric': 'accuracy', 'gpu_score': 1, 'target': {'relative': 1}}]},
               {'scores': [{'metric': 'accuracy', 'gpu_score': 1, 'sample_results': True}]}]
        for update in bad:
            task = raw()
            task['accuracy']['gpu_baselines'][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                normalize(task)

    def test_absent_empty_and_duplicate_details_preserve_total_score(self):
        for details in ([], [{'sample_id': 'q', 'score': 0}, {'sample_id': 'q', 'score': 1}]):
            task = raw()
            task['accuracy']['gpu_baselines'][0]['scores'][0]['sample_results'] = details
            value = normalize(task)['accuracy']['gpu_baselines'][0]['scores'][0]
            self.assertEqual(value['sample_results'], details)
            self.assertEqual(value['gpu_score'], 0)
