"""客户镜像默认值经公开准备操作选择；仅模拟公开操作的外部进程。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from coordination.runtime import sources
from customer_workflow import context, effects, runner
from customer_workflow.compile import assets
from customer_workflow.contract import normalize
from customer_workflow.state import Session


class CustomerImageBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'task.json'
        self.raw = {'model': {}, 'environment': {}, 'accuracy': {}}
        self.source.write_text(json.dumps(self.raw))
        self.task = normalize(self.raw)
        self.session = self.session_for('customer')
        self.ref = 'quay.io/ascend/vllm-ascend:v1.20.0rc2'
        self.digest = 'sha256:' + 'a' * 64
        self.selected = {'reference': self.ref, 'release': 'v1.20.0rc2', 'release_url': 'https://example.invalid',
                         'expected_digest': self.digest, 'source': 'registry', 'platform': 'linux/arm64'}
        self.target = {'platform': 'linux/arm64', 'vllm': '1.20.0', 'vllm_ascend': '1.20.0rc2',
                       'hardware': {'family': 'Atlas A2', 'name': 'Ascend910B'}}
        self.recipe = {'vllm': '1.20.0', 'vllm_ascend': '1.20.0rc2', 'source_ref': self.ref,
                       'evidence': ['https://example.invalid/version'], 'hardware_families': ['Atlas A2']}
        self.obs = {'target': self.target, 'version_review': [], 'model_resolution': {'request': {}},
                    'image_request': {'recipe': self.recipe}}
        self.network = self.enter_patch(patch.object(effects, 'call', side_effect=self.image_process))
        self.enter_patch(patch.object(effects, 'evaluator', return_value=None))

    def enter_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def session_for(self, run_id, previous=None):
        return Session(self.root / run_id, self.source, self.task, run_id, previous)

    def image_process(self, argv, timeout, folder, key):
        self.assertEqual(Path(argv[2]), ROOT / 'skills/vllm-migration-prepare/scripts/customer.py')
        request = sources.read(argv[4])
        self.assertEqual(request, {'operation': 'image', 'reference': -1, 'platform': 'linux/arm64'})
        return 0, json.dumps({'status': 'completed', 'operation': 'image', 'inputs': request, 'result': self.selected})

    def discover(self):
        runner.discover(self.session, self.task, self.obs, self.root)

    def compile_image(self):
        result = assets.preparation(self.task, self.obs, self.session)
        return sources.read(result['tasks']['image']['request'])

    def test_unset_uses_official_identity_and_preserves_partial_context(self):
        for index, env in enumerate([{}, {'image_name': None}, {'image_name': -1}]):
            with self.subTest(environment=env):
                self.task = normalize({**self.raw, 'environment': env})
                self.session = self.session_for('input-' + str(index))
                before = copy.deepcopy(self.obs)
                self.discover()
                self.assertEqual(self.compile_image(), {
                    'source': 'registry', 'platform': 'linux/arm64', 'reference': self.ref,
                    'expected_digest': self.digest, 'hardware': self.target['hardware'], 'recipe': self.recipe})
                self.assertEqual(self.obs, before)
        self.assertEqual(self.network.call_count, 3)

    def test_supplied_identity_and_recipe_must_match(self):
        self.discover()
        self.obs['image_request']['reference'] = self.ref.split(':')[0] + '@' + self.digest
        self.recipe['source_ref'] = self.obs['image_request']['reference']
        self.assertEqual(self.compile_image()['reference'], self.ref)
        for key, wrong in [('reference', 'example.invalid/stale:v1'), ('expected_digest', 'sha256:' + 'b' * 64),
                           ('source', 'reuse'), ('platform', 'linux/amd64'), ('hardware', {'name': 'other'})]:
            with self.subTest(key=key), patch.dict(self.obs['image_request'], {key: wrong}), self.assertRaisesRegex(ValueError, key):
                self.compile_image()
        for key, wrong in [('source_ref', self.ref.replace('rc2', 'rc1')), ('vllm_ascend', '1.20.0rc1'),
                           ('vllm', '1.19.0')]:
            with self.subTest(key=key), patch.dict(self.recipe, {key: wrong}), self.assertRaisesRegex(ValueError, key):
                self.compile_image()
        with patch.dict(self.target, {'vllm_ascend': '1.20.0rc1'}), self.assertRaisesRegex(ValueError, 'vllm_ascend'):
            self.compile_image()

    def test_only_a_verified_platform_config_id_can_enter_default_preparation(self):
        config_id = 'sha256:' + 'f' * 64
        self.obs['image_request']['expected_image_id'] = config_id
        self.discover()
        with self.assertRaisesRegex(ValueError, 'expected_image_id'):
            self.compile_image()
        self.selected['expected_image_id'] = config_id
        self.session = self.session_for('with-config-id')
        self.discover()
        self.assertEqual(self.compile_image()['expected_image_id'], config_id)
        with patch.dict(self.obs['image_request'], {'expected_image_id': self.digest}), self.assertRaisesRegex(
                ValueError, 'expected_image_id'):
            self.compile_image()

    def test_only_proven_index_digest_enters_image_preparation(self):
        index = 'sha256:' + 'd' * 64
        self.discover()
        with patch.dict(self.obs['image_request'], {'expected_index_digest': index}), self.assertRaisesRegex(
                ValueError, 'expected_index_digest'):
            self.compile_image()
        self.selected['tag_manifest_digest'] = index
        self.session = self.session_for('quay-with-index')
        self.discover()
        self.assertEqual(self.compile_image()['expected_index_digest'], index)
        with patch.dict(self.obs['image_request'], {'expected_index_digest': self.digest}), self.assertRaisesRegex(
                ValueError, 'expected_index_digest'):
            self.compile_image()

    def test_no_recipe_records_selection_and_resume_reuses_without_query(self):
        del self.obs['image_request']
        self.discover()
        selected = self.session.ready('image_resolution')
        self.assertEqual(selected['result'], self.selected)
        with self.assertRaises(KeyError):
            self.compile_image()
        self.session = self.session_for('customer', self.session.path)
        self.obs['image_request'] = {'recipe': self.recipe}
        self.discover()
        self.assertEqual(self.compile_image()['expected_digest'], self.digest)
        self.network.assert_called_once()

    def test_malformed_platform_can_be_completed_with_first_valid_observation(self):
        self.target['platform'] = 'arm64'
        self.discover()
        self.network.assert_not_called()
        self.assertNotIn('request', self.session.value['steps']['image_resolution'])
        self.target['platform'] = 'linux/arm64'
        self.discover()
        self.assertEqual(self.compile_image()['platform'], 'linux/arm64')

    def test_malformed_recipe_version_blocks_preparation_with_diagnostic(self):
        self.discover()
        self.target['vllm_ascend'] = self.recipe['vllm_ascend'] = None
        with self.assertRaisesRegex(ValueError, 'vllm_ascend'):
            self.compile_image()

    def test_platform_change_before_preparation_cannot_replace_first_selection(self):
        self.discover()
        original = self.session.value['steps']['image_resolution']['receipt']
        self.session = self.session_for('customer', self.session.path)
        self.target['platform'] = 'linux/amd64'
        self.discover()
        with self.assertRaisesRegex(ValueError, '平台|platform'):
            self.compile_image()
        self.assertEqual(self.session.value['steps']['image_resolution']['receipt'], original)
        self.network.assert_called_once()

    def test_discovery_failure_blocks_only_consumers_and_keeps_cause(self):
        self.network.side_effect = None
        self.network.return_value = 3, json.dumps({'status': 'blocked', 'reason': 'Quay TLS error'})
        with patch.object(runner, 'data_jobs', return_value=[]) as data, patch.object(runner, 'execute') as execute, \
                patch.object(runner.model_identity, 'verify', return_value={}), patch.object(runner, 'migrate') as migrate:
            code, value = runner.workflow(self.session, self.task, {'observations': self.obs}, self.root, None)
        self.assertEqual(code, 3)
        self.assertEqual(value['steps']['preparation']['status'], 'blocked')
        self.assertIn('Quay TLS error', str(value['steps']['image_resolution']))
        data.assert_called_once()
        migrate.assert_not_called()
        execute.assert_not_called()

    def historical_stage(self, image_status='completed'):
        # 阶段因另一资产阻塞，已验证的镜像保存在原始阶段和子任务回执中。
        image = {**self.obs['image_request'], 'source': 'registry', 'reference': self.ref,
                 'platform': 'linux/arm64', 'expected_digest': self.digest, 'hardware': self.target['hardware']}
        spec = {'request': self.session.artifact('prepare-image', image)['path'], 'required': True}
        inputs = {'purpose': 'deployment', 'target': self.target, 'tasks': {'image': spec}}
        origin = self.session.artifact('preparation', inputs)
        image = {**image, 'timeout_seconds': 600}
        base = self.root / 'customer'
        child = base / 'vllm-image-prepare/attempt/image-manifest.json'
        child.parent.mkdir(parents=True)
        asset = {'image_id': 'sha256:' + 'c' * 64, 'platform': image['platform'],
                 'repo_digests': [self.ref.split(':')[0] + '@' + self.digest]}
        verdict = 'pass' if image_status == 'completed' else 'fail'
        if image_status != 'completed':
            asset = None
        child.write_text(json.dumps({'schema_version': '1.0', 'task_id': 'vllm-image-prepare', 'run_id': 'customer',
                                    'status': image_status, 'inputs': image, 'asset': asset,
                                    'conclusions': {'assets': verdict}, 'evidence': {'manifest': str(child)},
                                    'uncovered': []}))
        row = {'status': image_status, 'request': image, 'asset': asset, 'conclusions': {'assets': verdict},
               'required': True, 'manifest': str(child), 'manifest_sha256': sources.ref(child)['sha256']}
        stage = base / 'vllm-migration-prepare/attempt/prepare-manifest.json'
        stage.parent.mkdir(parents=True)
        stage_status = 'failed' if image_status == 'failed' else 'blocked'
        stage.write_text(json.dumps({'schema_version': '1.0', 'task_id': 'vllm-migration-prepare', 'run_id': 'customer',
                                    'inputs': inputs, 'status': stage_status, 'tasks': {'image': row},
                                    'evidence': {'manifest': str(stage)}, 'conclusions': {'assets': 'not_evaluated'},
                                    'uncovered': []}))
        self.session.step('preparation', stage_status, receipt=sources.ref(stage), request=origin)
        self.session = self.session_for('customer', self.session.path)
        return child, image

    def test_partial_historical_stage_recovers_existing_image_without_reselection(self):
        child, original = self.historical_stage()
        self.discover()
        self.assertEqual(self.compile_image(), original)
        self.network.assert_not_called()
        with patch.dict(self.obs['image_request'], {'reference': self.ref + '-a3'}), self.assertRaisesRegex(ValueError, 'reference'):
            self.compile_image()
        self.session = self.session_for('customer', self.session.path)
        child.write_text('{}')
        self.discover()
        with self.assertRaisesRegex(ValueError, '新 run-id'):
            self.compile_image()
        self.network.assert_not_called()

    def test_failed_historical_pull_replays_only_the_fixed_request(self):
        _, original = self.historical_stage(image_status='failed')
        self.discover()
        self.assertEqual(self.compile_image(), original)
        self.assertEqual(self.session.ready('image_resolution')['historical_image_status'], 'failed')
        self.assertIsNone(self.session.ready('preparation'))
        self.network.assert_not_called()

    def test_unverifiable_legacy_image_cannot_lookup_quay(self):
        child, _ = self.historical_stage()
        child.unlink()
        self.discover()
        with self.assertRaisesRegex(ValueError, '新 run-id'):
            self.compile_image()
        self.network.assert_not_called()

    def test_unfrozen_lookup_failure_allows_corrected_platform(self):
        self.network.side_effect = None
        self.network.return_value = 3, json.dumps({'status': 'blocked', 'reason': 'TLS error', 'selection_frozen': False})
        self.target['platform'] = 'linux/amd64'
        self.discover()
        self.session = self.session_for('customer', self.session.path)
        self.target['platform'] = 'linux/arm64'
        self.network.side_effect = self.image_process
        self.discover()
        self.assertEqual(self.compile_image()['platform'], 'linux/arm64')
        self.assertEqual(self.network.call_count, 2)

    def test_explicit_json_or_prompt_and_completed_historical_preparation_skip_lookup(self):
        prompt = {'environment.image_name': 'local:explicit'}
        explicit = normalize({**self.raw, 'environment': {'image_name': 'local:explicit'}})
        for self.task in (explicit, context.declaration({'prompt_fields': prompt, 'intent': {'conflicts': []}}, self.raw)[0]):
            self.obs['image_request'] = {'reference': 'local:explicit'}
            self.discover()
            self.assertEqual(self.compile_image(), {'reference': 'local:explicit'})
        self.task = normalize(self.raw)
        self.session.step('preparation', 'completed', value={'status': 'completed'})
        self.discover()
        self.network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
