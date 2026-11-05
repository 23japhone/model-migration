"""动态默认绑定实际模型/主机证据，续接不能扩大已冻结选择。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_customer_contract import customer, SCRIPTS

sys.path.insert(0, str(SCRIPTS.parent / 'skills/vllm-migration-prepare/skills/vllm-model-prepare/scripts'))


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        from customer_workflow.contract import normalize
        from customer_workflow.state import Session
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.weights = self.root / 'weights'
        self.weights.mkdir()
        self.write('weights/config.json', {'model_type': 'synthetic', 'architectures': ['SyntheticModel']})
        self.write('weights/tokenizer.json', {'model': {'type': 'WordLevel', 'vocab': {'x': 0}}})
        self.write('weights/tokenizer_config.json', {'chat_template': '{{ messages }}'})
        (self.weights / 'pytorch_model.bin').write_bytes(b'offline synthetic weights')
        raw = customer()
        raw['model'] = {'path': str(self.weights)}
        raw['environment'].pop('available_npu_devices')
        self.source = self.write('task.json', raw)
        self.task = normalize(raw)
        self.session = Session(self.root / 'runs', self.source['path'], self.task, 'defaults')

    def write(self, path, value):
        from coordination.runtime.sources import ref
        path = self.root / path
        path.write_text(json.dumps(value))
        return ref(path)

    def model_observations(self, config=None):
        from model_prepare.customer import resolve
        if config is not None:
            self.write('weights/config.json', config)
        result = resolve(self.task['model'], remote_lookup=lambda *args: self.fail('no remote lookup'))
        return {'model_resolution': result}

    def inventory(self, ids=(0, 1), host='host-a'):
        source = self.write('host-' + host + '.json', {'status': 'completed', 'operation': 'observe',
            'result': {'docker_id': host, 'docker_name': host, 'npu_raw': 'synthetic complete NPU inventory'}})
        return {'device_inventory': {'source': source, 'complete': True, 'ids': list(ids)},
                'deployment': {'devices': [{'id': n, 'free_bytes': 900, 'total_bytes': 1000, 'occupied': False} for n in ids]}}

    def test_config_id_model_type_and_architecture_fallbacks(self):
        from customer_workflow.defaults.model import resolve
        for config, expected, kind in (
            ({'_name_or_path': 'org/model', 'model_type': 'synthetic'}, 'org/model', 'config_id'),
            ({'_name_or_path': '/local/model', 'model_type': 'synthetic'}, 'synthetic', 'model_type'),
            ({'architectures': ['SyntheticModel']}, 'SyntheticModel', 'architecture'),
        ):
            task = copy.deepcopy(self.task)
            self.session.value.pop('default_resolutions', None)
            resolve(self.session, task, self.model_observations(config))
            self.assertEqual(task['model']['model_name'], expected)
            self.assertRegex(task['environment']['container_name'], r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$')
            self.assertEqual(task['model']['model_repository_id'], -1)
            self.assertEqual(self.session.value['default_resolutions']['model']['origin'], kind)

    def test_no_unique_name_blocks_instead_of_inventing_one(self):
        from customer_workflow.defaults.model import resolve
        observations = self.model_observations({'architectures': ['First', 'Second']})
        with self.assertRaisesRegex(ValueError, 'model_name'):
            resolve(self.session, self.task, observations)
        self.assertEqual(self.task['model']['model_name'], -1)

    def test_verified_repository_precedes_config_and_conflict_blocks(self):
        from customer_workflow.defaults.model import resolve
        from customer_workflow.recovery import DecisionRequired
        self.task['model']['model_repository_id'] = 'org/model'
        observations = self.model_observations({'_name_or_path': 'base/model', 'model_type': 'base'})
        asset = observations['model_resolution']['asset']
        proof = self.write('provenance.json', {'repository': 'org/model'})
        review = {'repository': 'org/model', 'path': str(self.weights), 'identity': asset['identity'],
                  'conclusion': 'match', 'reason': 'offline review', 'evidence': [proof]}
        observations['model_identity_review'] = self.write('review.json', review)
        resolve(self.session, self.task, observations)
        self.assertEqual(self.task['model']['model_name'], 'org/model')
        self.task['model']['model_name'] = -1
        review['conclusion'] = 'conflict'
        observations['model_identity_review'] = self.write('conflict.json', review)
        with self.assertRaises(DecisionRequired):
            resolve(self.session, self.task, observations)

    def test_config_metadata_must_match_actual_file(self):
        from customer_workflow.defaults.model import resolve
        observations = self.model_observations()
        observations['model_resolution']['asset']['metadata']['config']['model_type'] = 'forged'
        with self.assertRaisesRegex(ValueError, 'config'):
            resolve(self.session, self.task, observations)

    def test_name_flows_to_all_consumers(self):
        from customer_workflow.defaults.model import resolve
        from customer_workflow.compile.deployment import command, cases
        from customer_workflow.compile.accuracy import task_config
        resolve(self.session, self.task, self.model_observations())
        self.task['environment']['available_npu_devices'] = [0]
        recipe = {'runtime_model_path': '/runtime/weights', 'runtime': {'python': '/bin/python'},
                  'dtype': 'bfloat16', 'max_num_seqs': 2}
        argv = command(self.task, {}, recipe, {'devices': [0], 'tensor_parallel_size': 1, 'memory_fraction': .8}, 8000, 9000)
        self.assertEqual(argv[argv.index('--served-model-name') + 1], 'synthetic')
        self.assertEqual({row['payload']['model'] for row in cases('synthetic', {'prompt': 'native', 'prompt_tokens': 1, 'chat_template_kwargs': {}})}, {'synthetic'})
        job = {'dataset': {'name': 'ceval', 'limit': None}, 'subset': {'name': 'a', 'split': 'val'},
               'catalog': {'local_path': '/data/ceval'}, 'prepared': {'asset': {'path': '/data/prepared'}}}
        config = task_config(self.task, job, 'http://127.0.0.1:8000', False)
        self.assertEqual(config['model'], 'synthetic')

    def test_complete_device_inventory_is_a_pool_not_fixed_tp(self):
        from customer_workflow.defaults.devices import resolve
        from customer_workflow.compile.deployment import allocation_request
        sys.path.insert(0, str(SCRIPTS.parent / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))
        from verification.customer.capacity import select
        observations = self.inventory((3, 1, 0, 2))
        resolve(self.session, self.task, observations, self.root / 'runs')
        self.assertEqual(self.task['environment']['available_npu_devices'], [0, 1, 2, 3])
        observations['deployment']['devices'][2]['occupied'] = True
        spec = {'weight_bytes': 1000, 'kv_bytes_per_token': 2, 'context_tokens': 100,
                'runtime_bytes_per_device': 100, 'allowed_tp': [1, 2, 4], 'memory_fraction': .9}
        request = allocation_request(self.task, observations['deployment'], spec)
        self.assertEqual(select(spec, request['devices'], -1)['devices'], [1, 2])

    def test_missing_incomplete_or_changed_inventory_cannot_choose_devices(self):
        from customer_workflow.defaults.devices import resolve
        with patch('customer_workflow.effects.operation', return_value={}) as operation:
            with self.assertRaisesRegex(ValueError, 'device_inventory'):
                resolve(self.session, self.task, {}, self.root / 'runs')
        self.assertEqual(operation.call_args.args[3]['operation'], 'observe')
        for observations in (self.inventory(()), self.inventory()):
            observations['device_inventory']['complete'] = False
            with self.assertRaises(ValueError):
                resolve(self.session, self.task, observations, self.root / 'runs')
        observations = self.inventory()
        Path(observations['device_inventory']['source']['path']).write_text('changed')
        with self.assertRaises(ValueError):
            resolve(self.session, self.task, observations, self.root / 'runs')

    def test_explicit_device_pool_is_not_expanded(self):
        from customer_workflow.defaults.devices import resolve
        self.task['environment']['available_npu_devices'] = [2]
        with patch('customer_workflow.effects.operation') as operation:
            resolve(self.session, self.task, {}, self.root / 'runs')
        operation.assert_not_called()
        self.assertEqual(self.task['environment']['available_npu_devices'], [2])

    def test_deployment_host_must_match_default_inventory_host(self):
        from customer_workflow.defaults.devices import resolve, check_host
        from customer_workflow import runner
        observations = self.inventory()
        observations['deployment']['model_max_context'] = 9000
        resolve(self.session, self.task, observations, self.root / 'runs')
        check_host(self.session, {'docker_id': 'host-a', 'docker_name': 'host-a'})
        with self.assertRaisesRegex(ValueError, '主机'):
            check_host(self.session, {'docker_id': 'host-b', 'docker_name': 'host-b'})
        with patch.object(self.session, 'ready', side_effect=[None, {'status': 'completed'}]), \
                patch.object(runner.deployment, 'prepared_model', return_value={}), \
                patch.object(runner.effects, 'precheck_plan', return_value={}), \
                patch.object(runner.effects, 'functional_inputs', return_value=({}, 9000)), \
                patch.object(runner.deployment, 'capacity_spec', return_value={}), \
                patch.object(runner.effects, 'operation', side_effect=[{}, {'docker_id': 'host-b', 'docker_name': 'host-b'}]), \
                patch.object(runner.recovery, 'container') as container, patch.object(runner, 'execute') as execute:
            with self.assertRaisesRegex(ValueError, '主机'):
                runner.migrate(self.session, self.task, observations, [], self.root / 'runs', {})
        container.assert_not_called()
        execute.assert_not_called()

    def test_first_resolution_can_resume_but_frozen_pool_cannot_expand(self):
        from customer_workflow.defaults import state, model, devices
        from customer_workflow.state import Session
        declaration = copy.deepcopy(self.task)
        state.begin(self.session, declaration)
        before = self.session.path.read_bytes()
        resumed = Session(self.root / 'runs', self.source['path'], declaration, 'defaults', self.session.path)
        state.begin(resumed, declaration)
        model.resolve(resumed, self.task, self.model_observations())
        devices.resolve(resumed, self.task, self.inventory(), self.root / 'runs')
        self.assertEqual(self.session.path.read_bytes(), before)
        again = Session(self.root / 'runs', self.source['path'], declaration, 'defaults', resumed.path)
        state.begin(again, declaration)
        with self.assertRaisesRegex(ValueError, '冻结'):
            devices.resolve(again, copy.deepcopy(declaration), self.inventory((0, 1, 2)), self.root / 'runs')

    def test_resume_without_context_does_not_lose_frozen_defaults(self):
        from customer_workflow.defaults import state, devices
        from customer_workflow.state import Session
        declaration = copy.deepcopy(self.task)
        state.begin(self.session, declaration)
        devices.resolve(self.session, self.task, self.inventory(), self.root / 'runs')
        middle = Session(self.root / 'runs', self.source['path'], declaration, 'defaults', self.session.path)
        middle.finish('blocked', next_action='context_required')
        again = Session(self.root / 'runs', self.source['path'], declaration, 'defaults', middle.path)
        state.begin(again, declaration)
        with self.assertRaisesRegex(ValueError, '冻结'):
            devices.resolve(again, declaration, self.inventory((0, 1, 2)), self.root / 'runs')

    def test_default_failures_keep_independent_preparation_and_block_deployment(self):
        from customer_workflow import runner
        with patch.object(runner, 'discover'), patch.object(runner, 'prepare', return_value={'status': 'completed'}) as prepare, \
                patch.object(runner, 'data_jobs', return_value=[]) as data, patch.object(runner, 'migrate') as migrate, \
                patch('customer_workflow.effects.operation', side_effect=ValueError('no host')):
            code, result = runner.workflow(self.session, self.task, {'observations': {
                'image_request': {}, 'target': {}, 'version_review': [], 'model_resolution': {}}}, self.root / 'runs',
                runner.context.installation({}, self.task))
        self.assertEqual(code, 3)
        prepare.assert_called_once()
        data.assert_called_once()
        migrate.assert_not_called()
        self.assertEqual(result['steps']['default_model']['status'], 'blocked')
        self.assertEqual(result['steps']['default_devices']['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
