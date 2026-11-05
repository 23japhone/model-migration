"""镜像准备应识别 Docker 按 digest 拉取后留下的本地引用。"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-migration-prepare'
sys.path.insert(0, str(ROOT / 'skills/vllm-image-prepare/scripts'))
sys.path.insert(0, str(ROOT / 'scripts'))
from image_prepare.docker import prepare_image
from image_prepare.request import normalize_request
from migration_prepare.asset_contract import image_contract


class RegistryCacheTests(unittest.TestCase):
    def setUp(self):
        repo = 'quay.io/ascend/vllm-ascend'
        self.digest = 'sha256:' + 'a' * 64
        self.pinned = repo + '@' + self.digest
        self.tag = repo + ':v1.0.0rc2'
        self.record = {'Id': 'sha256:' + 'b' * 64, 'Os': 'linux', 'Architecture': 'arm64',
                       'RepoDigests': [self.pinned]}
        self.images = {self.record['Id']: self.record}
        self.docker = Mock()
        self.docker.daemon.return_value = {'OSType': 'linux', 'Architecture': 'arm64', 'ID': 'test',
                                           'Name': 'docker', 'ServerVersion': '27'}
        self.docker.inspect.side_effect = lambda ref, missing_ok=False: self.images.get(ref)
        self.request = {'reference': self.tag, 'source': 'registry', 'expected_digest': self.digest,
                        'platform': 'linux/arm64', 'hardware': {}, 'recipe': {'source_ref': self.tag}}

    def test_matching_digest_without_original_tag_reuses_image(self):
        self.images[self.pinned] = self.record
        result = prepare_image(self.request, self.docker)
        self.assertEqual(result['operation'], 'reused')
        self.docker.invoke.assert_not_called()

    def test_conflicting_tag_is_preserved_even_if_pinned_copy_exists(self):
        self.images.update({self.tag: {**self.record, 'RepoDigests': []}, self.pinned: self.record})
        with self.assertRaisesRegex(ValueError, 'digest'):
            prepare_image(self.request, self.docker)
        self.docker.invoke.assert_not_called()

    def test_missing_image_pulls_once_and_next_preparation_reuses_it(self):
        def pull(argv):
            self.assertEqual(argv, ['image', 'pull', '--platform', 'linux/arm64', self.pinned])
            self.images[self.pinned] = self.record

        self.docker.invoke.side_effect = pull
        self.assertEqual(prepare_image(self.request, self.docker)['operation'], 'pulled')
        self.assertEqual(prepare_image(self.request, self.docker)['operation'], 'reused')
        self.docker.invoke.assert_called_once()

    def configure_index_cache(self):
        index = 'sha256:' + 'c' * 64
        self.index_ref = self.tag.split(':')[0] + '@' + index
        self.request['expected_index_digest'] = index
        self.request['recipe']['source_ref'] = self.pinned
        self.record['RepoDigests'] = [self.index_ref]
        self.images[self.tag] = self.record
        return {'Descriptor': {'platform': {'os': 'linux', 'architecture': 'arm64', 'variant': 'v8'},
                               'digest': self.digest},
                'SchemaV2Manifest': {'config': {'digest': self.record['Id']}}}

    def test_index_digest_without_local_tag_reuses_verified_child(self):
        row = self.configure_index_cache()
        self.images[self.index_ref] = self.images.pop(self.tag)
        self.docker.invoke.return_value = json.dumps([row])
        asset = prepare_image(self.request, self.docker)
        self.assertEqual(asset['operation'], 'reused')
        self.assertEqual(asset['index_manifest']['manifest_digest'], self.digest)
        self.docker.invoke.assert_called_once_with(['manifest', 'inspect', '--verbose', self.index_ref])

    def test_index_cache_verifies_platform_and_config_without_pull(self):
        row = self.configure_index_cache()
        for manifest in ('SchemaV2Manifest', 'OCIManifest'):
            with self.subTest(manifest=manifest):
                self.docker.invoke.reset_mock()
                self.docker.invoke.return_value = json.dumps([{'Descriptor': row['Descriptor'],
                                                               manifest: row['SchemaV2Manifest']}])
                asset = prepare_image(self.request, self.docker)
                self.docker.invoke.assert_called_once_with(['manifest', 'inspect', '--verbose', self.index_ref])
                self.assertEqual(asset['operation'], 'reused')
                self.assertEqual(asset['repo_digests'], [self.index_ref])
                self.assertEqual(asset['index_manifest'], {'reference': self.index_ref, 'manifest_digest': self.digest,
                                 'platform': 'linux/arm64', 'config_digest': self.record['Id']})
                image_contract(asset, self.request)

    def test_index_cache_rejects_missing_or_ambiguous_proof(self):
        row = self.configure_index_cache()
        wrong_platform = {'os': 'linux', 'architecture': 'arm64', 'variant': 'v9'}
        wrong = 'sha256:' + 'd' * 64
        for rows in ([row, row], [{'Descriptor': {**row['Descriptor'], 'platform': wrong_platform}}],
                     [{**row, 'Descriptor': {**row['Descriptor'],
                                             'platform': {'architecture': 'linux', 'variant': 'arm64'}}}],
                     [{**row, 'Descriptor': {**row['Descriptor'], 'digest': wrong}}],
                     [{**row, 'SchemaV2Manifest': {'config': {'digest': wrong}}}], [{'Descriptor': row['Descriptor']}]):
            with self.subTest(rows=rows):
                self.docker.invoke.reset_mock()
                self.docker.invoke.return_value = json.dumps(rows)
                with self.assertRaises(ValueError):
                    prepare_image(self.request, self.docker)
                self.docker.invoke.assert_called_once_with(['manifest', 'inspect', '--verbose', self.index_ref])

    def test_index_digest_is_rechecked_on_final_local_identity(self):
        self.docker.invoke.return_value = json.dumps([self.configure_index_cache()])
        self.images[self.record['Id']] = {**self.record, 'RepoDigests': []}
        with self.assertRaisesRegex(ValueError, 'index'):
            prepare_image(self.request, self.docker)

    def test_unrelated_local_index_blocks_without_lookup_or_pull(self):
        self.configure_index_cache()
        self.request['expected_index_digest'] = 'sha256:' + 'f' * 64
        with self.assertRaisesRegex(ValueError, 'digest'):
            prepare_image(self.request, self.docker)
        self.docker.invoke.assert_not_called()

    def test_stage_rechecks_index_proof_against_request_and_local_image(self):
        row = self.configure_index_cache()
        self.docker.invoke.return_value = json.dumps([row])
        asset = prepare_image(self.request, self.docker)
        proof = asset['index_manifest']
        for field in proof:
            with self.subTest(field=field), self.assertRaises(ValueError):
                image_contract({**asset, 'index_manifest': {**proof, field: 'incorrect'}}, self.request)
        with self.assertRaises(ValueError):
            image_contract({**asset, 'repo_digests': []}, self.request)
        legacy = dict(self.request)
        del legacy['expected_index_digest']
        with self.assertRaises(ValueError):
            image_contract(asset, legacy)

    def test_index_constraint_is_optional_and_only_valid_for_registry(self):
        example = ROOT / 'skills/vllm-image-prepare/assets/registry.example.json'
        base = json.loads(example.read_text())
        index = 'sha256:' + 'c' * 64
        self.assertNotIn('expected_index_digest', normalize_request(base))
        self.assertEqual(normalize_request({**base, 'expected_index_digest': index})['expected_index_digest'], index)
        for changes in ({'source': 'reuse', 'expected_index_digest': index}, {'expected_index_digest': 'invalid'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_request({**base, **changes})
