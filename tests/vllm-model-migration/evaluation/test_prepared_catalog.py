"""正式 catalog 必须消费已准备目录，不能沿用源目录的 token 预算。"""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts')]


class PreparedCatalogTests(unittest.TestCase):
    def test_catalog_failure_blocks_only_its_job_and_preserves_deployment_budget(self):
        from customer_workflow import runner
        from customer_workflow.compile.generation import deployment_context
        task = {'accuracy': {'datasets': [{'name': 'ceval'}]}}
        jobs = [{'id': 'dataset-0-' + str(i), 'subset': {'name': str(i)}} for i in range(2)]
        session = SimpleNamespace(step=lambda *args, **kwargs: None)
        ready = {'prepared': {'asset': {}}, 'receipt': {}, 'subset': {'name': '1', 'max_prompt_tokens': 17}}
        with patch.object(runner.assets, 'dataset_jobs', return_value=jobs), \
                patch.object(runner, 'prepare_dataset', return_value={'status': 'completed'}), \
                patch.object(runner, 'bind_prepared', side_effect=[ValueError('catalog bad'), ready]):
            actual = runner.data_jobs(session, task, {}, Path('/unused'))
        self.assertIn('blocked_reason', actual[0])
        self.assertNotIn('prepared', actual[0])
        self.assertEqual(actual[1]['subset']['max_prompt_tokens'], 17)
        self.assertEqual(deployment_context(actual, {'prompt_tokens': 8}, {'model_max_context': 16384}), 16384)

    def test_layout_upgrade_creates_new_receipt_without_changing_old_request(self):
        from customer_workflow.evaluation.datasets import reusable_request
        old = {'source': 'local', 'path': '/data', 'recipe': 'ceval', 'split': 'val'}
        new = {**old, 'layout': 'evalscope'}
        self.assertFalse(reusable_request(old, new))
        self.assertNotIn('layout', old)
        self.assertTrue(reusable_request(new, new))
        with self.assertRaisesRegex(ValueError, '请求'):
            reusable_request(old, {**new, 'split': 'test'})

    def test_native_config_binds_receipt_root_and_preserves_original_dataset_request(self):
        from precision.runtime.evalscope.catalog import base_config
        request = {'model': 'model', 'dataset': {'name': 'ceval', 'path': '/source',
                   'subset_list': ['accountant'], 'limit': 15}}
        before = copy.deepcopy(request)
        config = base_config(request, {'path': '/prepared'})
        self.assertEqual(config['dataset_args']['ceval']['local_path'], '/prepared')
        self.assertEqual(request, before)

    def test_final_catalog_replaces_provisional_lengths_and_records_receipt(self):
        from customer_workflow.evaluation.datasets import bind_prepared
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            reference = {'path': str(root / 'receipt.json'), 'sha256': 'a' * 64}
            prepared = {'asset': {'path': str(root)}}
            task = {'model': {'model_name': 'model'}, 'accuracy': {'evaluator': 'evalscope'}}
            job = {'id': 'dataset-0-0', 'dataset': {'name': 'ceval', 'path': '/source',
                   'subset_list': ['accountant'], 'limit': 15},
                   'subset': {'name': 'accountant', 'split': 'val', 'source_file': 'val/accountant_val.csv',
                              'max_prompt_tokens': 99999}, 'catalog': {'local_path': '/source'}}
            observations = {'evaluator': {'python': '/python', 'version': '1.14.1'},
                            'model_resolution': {'request': {'path': '/model'}}}
            session = SimpleNamespace(value={'steps': {job['id']: {'receipt': reference}}})
            def operation(session, key, owner, request, work_root, **kwargs):
                self.assertEqual(request['dataset_manifest'], reference)
                self.assertEqual(request['dataset']['path'], '/source')
                self.assertEqual(owner, 'verify-agent-plus')
                self.assertEqual(kwargs['entry'], 'catalog.py')
                return {'inputs': request, 'dataset_manifest': reference, 'local_path': str(root),
                        'data_files': {}, 'evaluator_version': '1.14.1', 'subset_names': ['accountant'],
                        'subsets': [{'name': 'accountant', 'split': 'val', 'max_prompt_tokens': 17}]}
            with patch('customer_workflow.evaluation.datasets.recovery.asset_files'), \
                    patch('customer_workflow.evaluation.datasets.assets.catalog_files'), \
                    patch('customer_workflow.evaluation.datasets.effects.operation', side_effect=operation):
                bound = bind_prepared(session, task, job, prepared, observations, root)
            self.assertEqual(bound['subset']['max_prompt_tokens'], 17)
            self.assertEqual(bound['prepared'], prepared)
            self.assertEqual(job['subset']['max_prompt_tokens'], 99999)

    def test_customer_evalscope_jobs_choose_native_layout_but_aisbench_keeps_raw(self):
        from customer_workflow.compile import assets
        dataset = {'name': 'ceval', 'path': '/source', 'subset_list': ['accountant'], 'limit': 15}
        catalog = {'source': {}, 'preparation': {'source': 'local', 'recipe': 'ceval', 'path': '/source'},
                   'subsets': [{'name': 'accountant', 'split': 'val', 'source_file': 'val/accountant_val.csv',
                                'max_prompt_tokens': 99999}]}
        observations = {'datasets': {'ceval': catalog}}
        for backend in ('evalscope', 'aisbench'):
            task = {'accuracy': {'datasets': [dataset], 'evaluator': backend}}
            with patch.object(assets, 'native_catalog', return_value={}):
                job = assets.dataset_jobs(task, observations)[0]
            self.assertEqual(job['request'].get('layout', 'raw'), 'evalscope' if backend == 'evalscope' else 'raw')
            if backend == 'evalscope':
                self.assertNotIn('max_prompt_tokens', job['subset'])


if __name__ == '__main__':
    unittest.main()
