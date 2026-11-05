"""真实生成器与发布器的合成证据联调；不替代 Ascend 实测。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-prepare/skills/vllm-dataset-prepare/scripts'))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        from test_container_registration_continuation import ContainerRegistrationContinuationTests
        self.case = ContainerRegistrationContinuationTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.entry, _ = self.case.make_entry()
        from tuning_handoff.generation import generate
        from coordination.runtime import sources
        self.configs = generate(self.entry, {'max_model_len': 9000, 'max_num_seqs': 4,
                                            'kv_token_capacity': 9000, 'runtime_metrics_available': False})
        self.request = {'schema_version': '1.0', 'entry_manifest': self.entry, **{
            key: sources.ref(self.case.write('generated/' + key + '.json', value))
            for key, value in self.configs.items()}}

    def test_generated_candidates_publish_as_one_directory_and_reuse(self):
        from tuning_handoff.cli import validated
        from tuning_handoff.flat_publication import publish
        from coordination.runtime.sources import bound
        configs = validated(self.request)
        root = self.case.base / 'customer-run'
        result = publish(self.request, configs, root, 'customer-run', lambda: validated(self.request))
        self.assertEqual({p.name for p in (root / 'deliverables').iterdir()},
                         {name + '.json' for name in self.configs})
        before = {p.name: p.read_bytes() for p in (root / 'deliverables').iterdir()}
        self.assertEqual(json.loads(Path(result['manifest']).read_text())['status'], 'ready')
        repeated = publish(self.request, configs, root, 'customer-run', lambda: validated(self.request))
        self.assertEqual(before, {p.name: p.read_bytes() for p in (root / 'deliverables').iterdir()})
        self.assertNotEqual(result['manifest'], repeated['manifest'])
        for key, source in repeated['deliverables'].items():
            self.assertEqual(bound(source), self.configs[key])

    def test_recheck_failure_never_publishes_partial_files(self):
        from tuning_handoff.flat_publication import publish
        root = self.case.base / 'failed-publish'
        def changed():
            raise ValueError('source changed')
        with self.assertRaises(ValueError):
            publish(self.request, self.configs, root, 'failed-publish', changed)
        self.assertFalse((root / 'deliverables').exists())
        self.assertFalse(list(root.glob('.deliverables-*')))
        self.assertFalse(list(root.rglob('export-manifest.json')))

    def test_host_cann_directory_cannot_replace_container_model_evidence(self):
        from tuning_handoff.runtime_environment import validate
        from coordination.runtime.sources import ref
        root = self.case.base
        cann = root / 'actual-host-cann'
        cann.mkdir()
        identity = {'pid': 123, 'pgid': 123, 'start_ticks': '456'}
        command = ['python', '-m', 'vllm.entrypoints.openai.api_server']
        original = {'runtime': {'env': {'ASCEND_HOME_PATH': str(cann)}}, 'service': {'command': command}}
        handoff = {'workers': {'listener_identities': [identity]}, 'model': {'runtime_root': '/container/model'}}
        handoff_path = self.case.write('path-check-handoff.json', handoff)
        processes = self.case.write('path-check-processes.json', [{'identity': identity, 'argv': command,
                                      'environment': {'ASCEND_HOME_PATH': str(cann)}}])
        binding = {'schema_version': '1.0', 'deployment': ref(handoff_path), 'processes': ref(processes),
                   'path_checks': {'identity': identity, 'paths': {str(cann): {'is_dir': True}}}}
        source = self.case.write('path-check-binding.json', binding)
        with self.assertRaisesRegex(ValueError, '部署模型路径'):
            validate(str(cann), original, handoff, ref(source))
        binding['path_checks']['paths']['/container/model'] = {'is_dir': True}
        source = self.case.write('path-check-complete.json', binding)
        validate(str(cann), original, handoff, ref(source))


class RequestCompilationTests(unittest.TestCase):
    def test_all_subsets_must_match_native_catalog_evidence(self):
        from customer_workflow.compile.assets import native_catalog
        from coordination.runtime.sources import ref
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            dataset = {'name': 'dataset', 'path': str(root / 'data'), 'subset_list': -1, 'limit': None}
            (root / 'data').mkdir()
            data = root / 'data' / 'samples.jsonl'
            data.write_text('{"question":"short"}\n')
            task = {'model': {'model_name': 'model'}}
            rows = [{'name': name, 'split': 'test', 'max_prompt_tokens': 100} for name in ('a', 'b')]
            path = root / 'catalog.json'
            path.write_text(json.dumps({'inputs': {'dataset': dataset, 'model': 'model', 'model_path': '/model'},
                'evaluator_version': '1.2.3', 'subset_names': ['a', 'b'], 'subsets': rows,
                'data_files': {'samples.jsonl': ref(data)['sha256']}}))
            catalog = {'source': ref(path), 'subsets': rows}
            observations = {'evaluator': {'version': '1.2.3'}, 'model_resolution': {'request': {'path': '/model'}}}
            native_catalog(task, dataset, catalog, observations)
            catalog['subsets'] = rows[:1]
            with self.assertRaisesRegex(ValueError, '完整'):
                native_catalog(task, dataset, catalog, observations)
            catalog['subsets'] = rows
            data.write_text('{"question":"content changed after token measurement"}\n')
            with self.assertRaisesRegex(ValueError, '数据内容'):
                native_catalog(task, dataset, catalog, observations)

    def test_generated_deployment_passes_public_shape_contract(self):
        from customer_workflow.compile.deployment import verify
        from verification.request import load
        from coordination.runtime.sources import ref
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            image = root / 'image.json'
            image.write_text(json.dumps({'asset': {'image_id': 'sha256:' + 'b' * 64}}))
            asset = {'path': str(root / 'model'), 'files': [
                {'path': name, 'sha256': 'a' * 64} for name in ('config.json', 'model.safetensors')],
                'weights': {'files': ['model.safetensors']}}
            recipe = {'runtime_model_path': '/model', 'model_max_context': 16000, 'dtype': 'bfloat16',
                'runtime': {'python': '/usr/bin/python3', 'versions': {'vllm': '1.0.0'},
                            'files': {'/packages/vllm.py': 'c' * 64}, 'env': {}},
                'mounts': [], 'control_devices': ['/dev/davinci_manager'],
                'container_resources': {'memory': '8g', 'shm_size': '1g', 'cpus': '2'},
                'registration_check': {'resources': '/resources', 'request': '/register.json'},
                'weight_markers': ['weights loaded'], 'max_num_seqs': 8}
            task = {'model': {'model_name': 'model'},
                    'environment': {'container_name': 'vllm_model', 'available_npu_devices': [0, 1, 2, 3]}}
            value = verify(task, asset, {'tasks': {'image': {'receipt': ref(image)}}}, {'deployment': recipe},
                           {'devices': [3], 'tensor_parallel_size': 1, 'memory_fraction': 0.8}, 8001, 9000,
                           {'prompt': 'native', 'prompt_tokens': 1, 'chat_template_kwargs': {}})
            path = root / 'verify.json'
            path.write_text(json.dumps(value))
            self.assertEqual(load(path), value)
            self.assertEqual(value['runtime']['env']['ASCEND_VISIBLE_DEVICES'], '3')
            self.assertNotIn('ASCEND_RT_VISIBLE_DEVICES', value['runtime']['env'])
            command = value['service']['command']
            self.assertEqual(command[command.index('--max-num-seqs') + 1], '8')

    def test_per_subset_selection_and_metadata_are_bound_without_expanding_limit(self):
        from dataset_prepare.request import normalize_request
        from dataset_prepare.assets import inspect_dataset
        from customer_workflow.compile.accuracy import task_config
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name, count in [('a', 2), ('b', 4)]:
                (root / (name + '.jsonl')).write_text(''.join(json.dumps({'x': n}) + '\n' for n in range(count)))
            (root / 'README.md').write_text('metadata')
            prepared = []
            for name in ('a', 'b'):
                request = normalize_request({'source': 'local', 'path': str(root), 'format': 'jsonl',
                    'split': 'test', 'files': ['a.jsonl', 'b.jsonl'], 'supporting_files': ['README.md'],
                    'selection': {'file': name + '.jsonl', 'limit': 3}})
                prepared.append(inspect_dataset(request))
            self.assertEqual([a['selection']['count'] for a in prepared], [2, 3])
            self.assertEqual(len(prepared[0]['files']), 3)
            job = {'dataset': {'name': 'dataset', 'limit': None}, 'subset': {'name': 'a', 'split': 'test'},
                   'catalog': {'local_path': str(root)}, 'prepared': {'asset': prepared[0]}}
            value = task_config({'model': {'model_name': 'model'}}, job, 'http://127.0.0.1:8000', True)
            self.assertIsNone(value['limit'])
            self.assertEqual(value['generation_config']['max_tokens'], 8192)
            self.assertEqual(value['dataset_args']['dataset']['few_shot_num'], 0)


if __name__ == '__main__':
    unittest.main()
