"""离线核验客户镜像发现、平台身份与同任务续接。"""
import hashlib
from http.client import IncompleteRead
import io
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-migration-prepare/scripts'
sys.path.insert(0, str(SCRIPTS))

from customer_assets import image

REPO = 'quay.io/ascend/vllm-ascend'
INDEX = 'sha256:' + 'a' * 64
ARM = 'sha256:' + 'b' * 64
AMD = 'sha256:' + 'c' * 64


def page(number, names, more=False):
    return {'page': number, 'has_additional': more,
            'tags': [{'name': name, 'manifest_digest': INDEX} for name in names]}


def descriptor(architecture, digest, variant=None):
    platform = {'os': 'linux', 'architecture': architecture}
    if variant:
        platform['variant'] = variant
    return {'Descriptor': {'platform': platform, 'digest': digest}}


def manifests(*records):
    return Mock(returncode=0, stdout=json.dumps(records), stderr='')


class QuayResolutionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pages = {}
        self.requests = []
        network = patch('urllib.request.OpenerDirector.open', side_effect=self.open_page)
        network.start()
        self.addCleanup(network.stop)
        docker = patch.object(image, 'call', return_value=manifests(
            descriptor('amd64', AMD), descriptor('arm64', ARM, 'v8')))
        self.docker = docker.start()
        self.addCleanup(docker.stop)

    def open_page(self, request, *args):
        url = urlsplit(request.full_url)
        self.assertEqual(url.netloc, 'quay.io')
        self.assertEqual(url.path, '/api/v1/repository/ascend/vllm-ascend/tag/')
        query = parse_qs(url.query)
        self.assertEqual(query['onlyActiveTags'], ['true'])
        num = int(query['page'][0])
        self.requests.append(num)
        row = self.pages[num]
        if isinstance(row, Exception):
            raise row
        if isinstance(row, io.BytesIO):
            return row
        return io.BytesIO(json.dumps(row).encode())

    def resolve(self, name='first', platform='linux/arm64', reference=-1):
        attempt = self.root / name
        attempt.mkdir()
        return image.resolve(reference, platform, attempt)

    def test_highest_numbered_base_tag_across_pages_is_bound_before_platform_inspect(self):
        self.pages = {1: page(1, ['nightly-releases-v99.0.0rc', 'v9.9.9.post4', 'v10.0.0rc2'], True),
                      2: page(2, ['v10.0.0rc10', 'v11.0.0-a3', 'v11.0.0-openeuler',
                                   'v11.0.0-310p', 'v11.0.0.dev1', 'v11.0.0alpha1', 'latest'])}
        selected = self.resolve()
        self.assertEqual(self.requests, [1, 2])
        self.assertEqual(selected['reference'], REPO + ':v10.0.0rc10')
        self.assertEqual(selected['release'], 'v10.0.0rc10')
        self.assertIn('quay.io/', selected['release_url'])
        self.assertEqual(selected['expected_digest'], ARM)
        self.assertEqual(selected['tag_manifest_digest'], INDEX)
        self.docker.assert_called_once_with(['docker', 'manifest', 'inspect', '--verbose', REPO + '@' + INDEX], 60)
        for i, response in self.pages.items():
            path = self.root / 'first' / f'quay-tags-page-{i}.json'
            self.assertEqual(json.loads(path.read_text()), response)

    def test_default_registry_image_id_comes_from_selected_platform_config(self):
        self.pages = {1: page(1, ['v1.0.0'])}
        for key in ('SchemaV2Manifest', 'OCIManifest'):
            with self.subTest(key=key), tempfile.TemporaryDirectory(dir=self.root) as folder:
                attempt = Path(folder) / 'attempt'
                attempt.mkdir()
                amd = {**descriptor('amd64', AMD), key: {'config': {'digest': INDEX}}}
                arm = {**descriptor('arm64', ARM), key: {'config': {'digest': AMD}}}
                self.docker.return_value = manifests(amd, arm)
                selected = image.resolve(-1, 'linux/arm64', attempt)
                self.assertEqual(selected['expected_digest'], ARM)
                self.assertEqual(selected['expected_image_id'], AMD)
                self.assertNotEqual(selected['expected_image_id'], selected['tag_manifest_digest'])

    def test_explicit_registry_reference_preserves_prior_result_fields(self):
        record = {**descriptor('arm64', ARM), 'OCIManifest': {'config': {'digest': AMD}}}
        self.docker.side_effect = [Mock(returncode=1, stderr='No such image: sample'),
                                   manifests(record)]
        selected = self.resolve(reference=REPO + ':v1.0.0')
        self.assertEqual(selected['expected_digest'], ARM)
        self.assertNotIn('expected_image_id', selected)
        self.assertEqual(self.requests, [])

    def test_final_and_post_order(self):
        for i, (tags, wanted) in enumerate([
            (['v1.20.1rc20', 'v1.20.1'], 'v1.20.1'),
            (['v1.20.1.post10', 'v1.20.1.post2', 'v1.20.1'], 'v1.20.1.post10'),
        ]):
            with self.subTest(wanted=wanted), tempfile.TemporaryDirectory(dir=self.root) as work:
                self.pages = {1: page(1, tags)}
                attempt = Path(work) / str(i)
                attempt.mkdir()
                self.assertEqual(image.resolve(-1, 'linux/amd64', attempt)['release'], wanted)

    def test_partial_or_malformed_pages_do_not_select_a_lower_version(self):
        bad_responses = (OSError('quay unavailable'), {'page': 2, 'tags': []}, page(1, []))
        for i, response in enumerate(bad_responses):
            with self.subTest(response=response):
                self.pages = {1: page(1, ['v1.2.3'], True), 2: response}
                with self.assertRaises((OSError, ValueError)):
                    self.resolve(str(i))
        self.docker.assert_not_called()
        self.assertFalse(list(self.root.glob('image-selection-*.json')))

    def test_empty_candidates_and_malformed_winning_digest_block_discovery(self):
        for i, tags in enumerate(([{'name': 'v1.0.0-a3', 'manifest_digest': INDEX}],
                                  [{'name': 'v1.0.0', 'manifest_digest': INDEX},
                                   {'name': 'v1.0.1rc1', 'manifest_digest': 'invalid'}])):
            self.pages = {1: {**page(1, []), 'tags': tags}}
            with self.assertRaises(ValueError):
                self.resolve(str(i))
        self.docker.assert_not_called()

    def test_missing_platform_or_invalid_digest_keeps_tag_frozen(self):
        self.pages = {1: page(1, ['v1.0.0rc1'])}
        self.docker.return_value = manifests(descriptor('amd64', AMD))
        with self.assertRaisesRegex(ValueError, '平台'):
            self.resolve()
        self.pages.clear()
        self.docker.return_value = manifests(descriptor('arm64', 'bad'))
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.resolve('second')
        self.docker.return_value = manifests(descriptor('arm64', ARM))
        selected = self.resolve('third')
        self.assertEqual(selected, self.resolve('fourth'))
        self.assertEqual((self.requests, self.docker.call_count), ([1], 3))
        self.assertEqual(selected['release'], 'v1.0.0rc1')

    def test_exact_variant_and_ambiguous_platform(self):
        self.pages = {1: page(1, ['v1.0.0'])}
        self.docker.return_value = manifests(descriptor('arm64', ARM, 'v8'), descriptor('arm64', AMD, 'v9'))
        self.assertEqual(self.resolve(platform='linux/arm64/v8')['expected_digest'], ARM)
        self.assertEqual(self.resolve('base')['expected_digest'], ARM)
        self.docker.return_value = manifests(descriptor('amd64', AMD), descriptor('amd64', ARM))
        with self.assertRaisesRegex(ValueError, '平台'):
            self.resolve('ambiguous', platform='linux/amd64')

    def test_windows_lock_path_keeps_cached_resolution_offline(self):
        windows = Mock()
        self.pages = {1: page(1, ['v1.0.0'])}
        selected = self.resolve()
        with patch.object(sys, 'platform', 'win32'), patch.dict(sys.modules, {'msvcrt': windows}):
            self.assertEqual(selected, self.resolve('resumed'))
        windows.locking.assert_called_once()
        self.assertEqual((self.requests, self.docker.call_count), ([1], 1))

    def test_malformed_manifest_returns_diagnostic_instead_of_an_unhandled_error(self):
        self.pages = {1: page(1, ['v1.0.0'])}
        self.docker.return_value = manifests({'Descriptor': None})
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.resolve()

    def test_conflicting_tag_digests_across_pages_prevent_freezing(self):
        self.pages = {1: page(1, ['v1.0.0'], True), 2: page(2, ['v1.0.0'])}
        self.pages[2]['tags'][0]['manifest_digest'] = AMD
        with self.assertRaisesRegex(ValueError, '标签身份'):
            self.resolve()
        self.docker.assert_not_called()
        self.assertFalse(list(self.root.glob('image-selection-*.json')))

    def test_existing_tag_only_selection_preserves_original_release_and_full_identity(self):
        platform = 'linux/arm64'
        key = hashlib.sha256(json.dumps([-1, platform]).encode()).hexdigest()
        frozen = self.root / f'image-selection-{key}.json'
        old = {'release': 'v0.1.0', 'release_url': 'https://example.org/old', 'reference': REPO + ':v0.1.0'}
        frozen.write_text(json.dumps(old))
        selected = self.resolve()
        self.docker.assert_called_once_with(['docker', 'manifest', 'inspect', '--verbose', old['reference']], 60)
        self.assertEqual(selected, {**old, 'source': 'registry', 'platform': platform, 'expected_digest': ARM})
        self.assertEqual(selected, self.resolve('second'))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.docker.call_count, 1)

    def failed_operation(self, run_id):
        from customer_assets import cli
        request = self.root / (run_id + '-request.json')
        request.write_text(json.dumps({'operation': 'image', 'reference': -1, 'platform': 'linux/arm64'}))
        args = ['customer.py', '--request', str(request), '--work-root', str(self.root / 'work'), '--run-id', run_id]
        stdout = io.StringIO()
        with patch.object(sys, 'argv', args), redirect_stdout(stdout):
            self.assertEqual(cli.main(), 3)
        report = json.loads(stdout.getvalue())
        self.assertEqual(json.loads(Path(report['manifest']).read_text()), report)
        return report

    def test_public_failure_records_whether_a_platform_selection_can_be_corrected(self):
        self.pages = {1: OSError('Quay offline')}
        before = self.failed_operation('registry-down')
        self.assertIs(before['selection_frozen'], False)
        self.pages = {1: page(1, ['v1.2.3'])}
        self.docker.return_value = manifests(descriptor('amd64', AMD))
        after = self.failed_operation('wrong-platform')
        self.assertIs(after['selection_frozen'], True)

    def test_interrupted_http_page_keeps_failure_receipt_and_partial_evidence(self):
        interrupted = io.BytesIO()
        interrupted.read = Mock(side_effect=IncompleteRead(b'{', 25))
        self.pages = {1: page(1, ['v1.2.3'], True), 2: interrupted}
        report = self.failed_operation('truncated-page')
        self.assertIs(report['selection_frozen'], False)
        self.assertIn('第 2 页', report['reason'])
        self.assertIn('IncompleteRead', report['reason'])
        attempt = Path(report['manifest']).parent
        self.assertEqual(json.loads((attempt / 'quay-tags-page-1.json').read_text()), self.pages[1])
        self.docker.assert_not_called()

    def test_public_failure_without_access_to_selection_does_not_claim_it_is_unfrozen(self):
        with patch.object(image, 'selection_lock', side_effect=PermissionError('cannot lock')):
            unknown = self.failed_operation('lock-denied')
        self.assertNotIn('selection_frozen', unknown)

    def test_invalid_existing_freeze_is_an_unknown_state(self):
        # 先用公开操作取得该 run 的证据父目录，再模拟历史损坏文件。
        self.pages = {1: page(1, ['v1.2.3'])}
        self.docker.return_value = manifests(descriptor('amd64', AMD))
        report = self.failed_operation('legacy-invalid')
        parent = Path(report['manifest']).parents[1]
        frozen, = parent.glob('image-selection-*.json')
        frozen.write_text('[]')
        result = self.failed_operation('legacy-invalid')
        self.assertNotIn('selection_frozen', result)

    def test_competing_attempts_share_one_frozen_selection(self):
        self.pages = {1: page(1, ['v1.0.0'])}
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(self.resolve, ['one', 'two']))
        self.assertEqual(values[0], values[1])
        self.assertEqual((self.requests, self.docker.call_count), ([1], 1))


if __name__ == '__main__':
    unittest.main()
