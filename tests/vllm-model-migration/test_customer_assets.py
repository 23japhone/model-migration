"""模型来源选择、环境隔离及默认资源的业务行为。"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / 'skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-model-prepare/scripts'
sys.path.insert(0, str(MODEL))
sys.path.insert(0, str(ROOT / 'skills/vllm-model-migration/scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-model-migration/skills/vllm-migration-prepare/scripts'))


class ModelResolutionTests(unittest.TestCase):
    def test_mutable_repository_change_is_rejected_before_download(self):
        from model_prepare.request import normalize_request
        from model_prepare.sources import prepare_remote
        request = normalize_request({'source': 'modelscope', 'repository': 'owner/name', 'path': '/missing-model',
                                     'scope': 'full', 'task': 'chat', 'expected_snapshot': 'a' * 64})
        provider = Mock()
        provider.describe.return_value = {'source_files': [{'path': 'config.json', 'size': 2,
                                                           'sha256': 'b' * 64}]}
        with patch('model_prepare.sources.ModelScopeSource', return_value=provider):
            with self.assertRaisesRegex(ValueError, '首次冻结'):
                prepare_remote(request)
        provider.download.assert_not_called()

    def test_local_model_discovery_requires_identity_review_without_download(self):
        from model_prepare.customer import resolve
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve()
            inspect = Mock(return_value={'path': str(path)})
            remote = Mock(side_effect=AssertionError('local weights must not query providers'))
            result = resolve({'path': str(path), 'model_repository_id': 'different/model'}, [], inspect, remote)
            self.assertEqual(result['request']['source'], 'local')
            self.assertNotIn('repository', result['request'])
            self.assertEqual(result['resolution'], 'local_identity_review_required')
            remote.assert_not_called()

    def test_only_confirmed_absence_switches_provider(self):
        from model_prepare.customer import resolve
        lookup = Mock(side_effect=[None, {'revision': 'a' * 40}])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp).resolve() / 'missing')
            result = resolve({'path': path, 'model_repository_id': 'org/model'}, [], Mock(), lookup)
            self.assertEqual(result['request']['source'], 'huggingface')
            self.assertEqual([call.args[0] for call in lookup.call_args_list], ['modelscope', 'huggingface'])
        lookup = Mock(side_effect=ConnectionError('network unavailable'))
        with self.assertRaises(ConnectionError):
            resolve({'path': '/missing-model', 'model_repository_id': 'org/model'}, [], Mock(), lookup)
        self.assertEqual(lookup.call_count, 1)

    def test_absent_local_repository_default_and_ambiguous_cache(self):
        from model_prepare.customer import resolve
        with self.assertRaisesRegex(ValueError, 'model_repository_id'):
            resolve({'path': '/missing-model', 'model_repository_id': -1}, [], Mock(), Mock())
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp).resolve() / 'a', Path(tmp).resolve() / 'b'
            a.mkdir(); b.mkdir()
            with self.assertRaisesRegex(ValueError, '多个'):
                resolve({'path': -1, 'model_repository_id': -1}, [a, b], Mock(return_value={}), Mock())

    def test_cache_links_must_stay_in_content_addressed_repository(self):
        from model_cache import checked
        from model_prepare.inventory import checked_file
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            repo = base / 'models--owner--name'
            snapshot = repo / 'snapshots' / ('a' * 40)
            snapshot.mkdir(parents=True)
            (repo / 'blobs').mkdir()
            blob = repo / 'blobs' / ('b' * 64)
            blob.write_text('tokenizer bytes')
            link = snapshot / 'tokenizer.json'
            link.symlink_to(blob)
            self.assertEqual(checked(snapshot, link.name), blob)
            self.assertEqual(checked_file(snapshot, link.name), link)
            link.unlink()
            outside = base / 'other.json'
            outside.write_text('different')
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                checked(snapshot, link.name)


class EnvironmentResolutionTests(unittest.TestCase):
    def test_failed_install_does_not_resolve_latest_again(self):
        from customer_assets.environment import selection
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            first, second = parent / 'first', parent / 'second'
            first.mkdir(); second.mkdir()
            with patch('customer_assets.environment.resolve_versions', return_value={'ais-bench-benchmark': '1.2.3'}) as resolve:
                original = selection({'packages': ['ais-bench-benchmark']}, parent, first)
                resumed = selection({'packages': ['ais-bench-benchmark']}, parent, second)
                self.assertEqual(original, resumed)
                resolve.assert_called_once()

    def test_explicit_local_image_avoids_remote_manifest_lookup(self):
        from customer_assets.image import resolve
        import json
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            attempt = parent / 'attempt'
            attempt.mkdir()
            result = Mock(returncode=0, stdout=json.dumps([{'Id': 'sha256:' + 'a' * 64,
                                                           'Os': 'linux', 'Architecture': 'arm64'}]))
            with patch('customer_assets.image.call', return_value=result) as call:
                value = resolve('example/vllm-ascend:v1.0.0', 'linux/arm64', attempt)
                self.assertEqual(value['source'], 'reuse')
                self.assertEqual(call.call_count, 1)
                self.assertEqual(call.call_args.args[0][1:3], ['image', 'inspect'])


if __name__ == '__main__':
    unittest.main()
