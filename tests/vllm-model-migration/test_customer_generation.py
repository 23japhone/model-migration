"""从真实基线生成交付配置，不复用示例参数。"""
import copy
import json
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/vllm-model-migration/scripts'
sys.path.insert(0, str(SCRIPTS))


class GenerationTests(unittest.TestCase):
    def test_benchmark_capacity_limits_and_context(self):
        from tuning_handoff.generation import workload
        plan = workload({'max_model_len': 1600, 'max_num_seqs': 12, 'kv_token_capacity': 10000,
                         'runtime_metrics_available': True})
        self.assertLessEqual(plan['benchmark']['input_tokens'] + plan['benchmark']['output_tokens'], 1600)
        self.assertEqual(plan['benchmark']['concurrency'], [1, 2, 4])
        self.assertEqual(plan['benchmark']['requests_per_run'], 32)
        self.assertEqual(plan['benchmark']['seed'], 42)
        self.assertTrue(plan['runtime_metrics']['enabled'])
        with self.assertRaises(ValueError):
            workload({'max_model_len': 1024, 'max_num_seqs': 8, 'kv_token_capacity': 100,
                      'runtime_metrics_available': False})

    def test_public_export_generation_preserves_actual_conditions(self):
        from tuning_handoff.generation import generate
        from test_container_registration_continuation import ContainerRegistrationContinuationTests
        case = ContainerRegistrationContinuationTests()
        case.setUp()
        try:
            entry, _ = case.make_entry()
            value = generate(entry, {'max_model_len': 8192, 'max_num_seqs': 4, 'kv_token_capacity': 16384,
                                     'runtime_metrics_available': False})
            self.assertEqual(value['accuracy_plan']['evaluation']['model_path'], case.handoff['model']['name'])
            self.assertEqual(value['deployment']['server']['model_path'], case.handoff['model']['runtime_root'])
            self.assertIsNone(value['accuracy_plan']['evaluation']['datasets'][0]['limit'])
        finally:
            case.doCleanups()


if __name__ == '__main__':
    unittest.main()
