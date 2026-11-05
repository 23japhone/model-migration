"""指定路径缺失时先查官方默认目录；仅在确实缺失时下载到指定路径。"""
import copy
from dataclasses import dataclass
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
DATA = ROOT / 'skills/vllm-migration-prepare/skills/vllm-dataset-prepare/scripts'
PRECISION = ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'
sys.path[:0] = [str(DATA), str(PRECISION), str(ROOT / 'scripts')]
spec = importlib.util.spec_from_file_location('dataset_resolution_entry', DATA / 'prepare.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
from dataset_prepare.request import normalize_request
from dataset_prepare.results import output_parent


def gsm8k(root):
    root.mkdir(parents=True)
    for split in ('train', 'test'):
        (root / (split + '.jsonl')).write_text(json.dumps({'question': 'one plus one?', 'answer': 'two #### 2'}) + '\n')


class Response(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url
        self.headers = {'Content-Length': str(len(content))}


class DatasetSourceResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.default = self.root / 'official-default'
        self.requested = self.root / 'requested'
        self.work = self.root / 'work'
        evidence = self.root / 'official-source.txt'
        evidence.write_text('Synthetic official-source observation: default path=' + str(self.default))
        self.reference = {'path': str(evidence), 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}
        self.request = {'source': 'recipe', 'recipe': 'gsm8k', 'path': str(self.requested),
                        'split': 'test', 'selection': {'limit': 15},
                        'default_local': {'path': str(self.default), 'evidence': self.reference}}

    def execute(self, request=None):
        request = normalize_request(copy.deepcopy(request or self.request))
        parent = output_parent(request, self.work, 'fixture')
        code, printed = entry.execute(request, parent, 'fixture')
        report = json.loads(Path(printed['manifest']).read_text())
        return code, report

    def response(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            for split in ('train', 'test'):
                archive.writestr('gsm8k/' + split + '.jsonl', json.dumps({'question': 'downloaded question', 'answer': '#### 3'}) + '\n')
        return Response(content.getvalue(), 'https://opencompass.oss-cn-shanghai.aliyuncs.com/datasets/data/gsm8k.zip')

    def test_reuses_readable_default_without_creating_requested_directory_or_downloading(self):
        gsm8k(self.default)
        before = {p.name: p.read_bytes() for p in self.default.iterdir()}
        with patch('dataset_prepare.sources.open_source', side_effect=AssertionError('unexpected download')) as network:
            code, report = self.execute()
        self.assertEqual(code, 0, report)
        network.assert_not_called()
        self.assertFalse(self.requested.exists())
        self.assertEqual(report['inputs']['path'], str(self.requested))
        self.assertEqual(report['asset']['path'], str(self.default))
        self.assertEqual(report['asset']['selection']['count'], 1)
        self.assertEqual(report['asset']['source']['kind'], 'local')
        self.assertEqual(report['asset']['source']['upstream_status'], 'unverified')
        self.assertEqual(report['asset']['source']['resolution']['decision'], 'reused_default')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.default.iterdir()})

    def test_reused_default_flows_into_evalscope_layout_and_customer_path_binding(self):
        from customer_workflow.compile import accuracy, assets
        gsm8k(self.default)
        (self.default / 'test_socratic.jsonl').write_text('unused variant')
        request = {**self.request, 'layout': 'evalscope'}
        with patch('dataset_prepare.sources.open_source', side_effect=AssertionError('unexpected download')):
            code, report = self.execute(request)
        self.assertEqual(code, 0, report)
        final = Path(report['asset']['path'])
        self.assertTrue(final.is_relative_to(self.work))
        self.assertEqual({p.name for p in final.iterdir()}, {'README.md', 'train.jsonl', 'test.jsonl'})
        dataset = {'name': 'gsm8k', 'path': str(self.requested), 'limit': 15}
        subset = {'name': 'default', 'split': 'test', 'source_file': 'test.jsonl'}
        catalog = {'preparation': request}
        compiled = assets.dataset_request(dataset, catalog, subset)
        self.assertEqual(compiled['path'], str(self.requested))
        self.assertEqual(compiled['default_local'], request['default_local'])
        task = {'model': {'model_name': 'fixture'}}
        config = accuracy.task_config(task, {'dataset': dataset, 'subset': subset, 'catalog': catalog,
                                            'prepared': report}, 'http://127.0.0.1:1', False)
        self.assertEqual(config['dataset_args']['gsm8k']['local_path'], str(final))

    def test_missing_default_downloads_and_extracts_only_under_requested_path(self):
        with patch('dataset_prepare.sources.open_source', return_value=self.response()) as network:
            code, report = self.execute()
        self.assertEqual(code, 0, report)
        network.assert_called_once()
        self.assertFalse(self.default.exists())
        self.assertTrue((self.requested / 'source-payload').is_file())
        self.assertTrue(Path(report['asset']['path']).is_relative_to(self.requested))
        self.assertEqual(report['asset']['source']['resolution']['decision'], 'download_requested')

    def test_ceval_default_is_read_before_building_native_metadata(self):
        from .test_dataset_layout import ceval
        ceval(self.default)
        request = {**self.request, 'recipe': 'ceval', 'split': 'val', 'subjects': ['accountant'], 'layout': 'evalscope'}
        with patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute(request)
        self.assertEqual(code, 0, report)
        network.assert_not_called()
        self.assertEqual(report['asset']['selection']['count'], 15)
        self.assertTrue((Path(report['asset']['path']) / 'README.md').is_file())
        self.assertFalse((self.default / 'README.md').exists())

    def test_generic_default_file_does_not_read_unrelated_files_in_its_parent(self):
        self.default.mkdir()
        chosen = self.default / 'selected.jsonl'
        chosen.write_text('{"question":"chosen"}\n')
        (self.default / 'unrelated.jsonl').write_text('not json')
        request = {**self.request, 'source': 'http', 'recipe': 'generic', 'format': 'jsonl',
                   'url': 'https://example.invalid/dataset.jsonl',
                   'default_local': {'path': str(chosen), 'evidence': self.reference}}
        with patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute(request)
        self.assertEqual(code, 0, report)
        network.assert_not_called()
        self.assertEqual([row['path'] for row in report['asset']['files']], ['selected.jsonl'])

    def test_no_default_preserves_existing_download_behavior(self):
        request = {k: v for k, v in self.request.items() if k != 'default_local'}
        with patch('dataset_prepare.sources.open_source', return_value=self.response()) as network:
            code, report = self.execute(request)
        self.assertEqual(code, 0, report)
        network.assert_called_once()
        self.assertTrue(Path(report['asset']['path']).is_relative_to(self.requested))

    def test_existing_requested_download_keeps_priority_over_default(self):
        with patch('dataset_prepare.sources.open_source', return_value=self.response()) as network:
            code, first = self.execute()
            self.assertEqual(code, 0, first)
            gsm8k(self.default)
            code, second = self.execute()
        self.assertEqual(code, 0, second)
        network.assert_called_once()
        self.assertEqual(first['asset']['path'], second['asset']['path'])
        self.assertEqual(second['asset']['operation'], 'reused')

    def test_invalid_existing_default_is_not_treated_as_missing(self):
        for kind in ('empty', 'missing_train', 'invalid_answer', 'ambiguous'):
            with self.subTest(kind=kind):
                candidate = self.root / kind
                if kind == 'empty':
                    candidate.mkdir()
                elif kind == 'ambiguous':
                    gsm8k(candidate / 'one'); gsm8k(candidate / 'two')
                else:
                    gsm8k(candidate)
                    if kind == 'missing_train':
                        (candidate / 'train.jsonl').unlink()
                    else:
                        (candidate / 'test.jsonl').write_text('{"question":"q","answer":"bad"}\n')
                request = {**self.request, 'default_local': {'path': str(candidate), 'evidence': self.reference}}
                with patch('dataset_prepare.sources.open_source') as network:
                    code, report = self.execute(request)
                self.assertEqual(code, 1, report)
                self.assertIn('default_local_unreadable', report['diagnostic']['message'])
                self.assertIn(str(candidate), report['diagnostic']['message'])
                network.assert_not_called()
                self.assertFalse(self.requested.exists())

    def test_permission_failure_does_not_download(self):
        gsm8k(self.default)
        with patch('dataset_prepare.assets.checked_files', side_effect=PermissionError('fixture denied')), \
                patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute()
        self.assertEqual(code, 1, report)
        self.assertIn('PermissionError', report['diagnostic']['message'])
        network.assert_not_called()

    def test_changed_official_evidence_prevents_reuse_and_download(self):
        Path(self.reference['path']).write_text('changed')
        with patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute()
        self.assertEqual(code, 1, report)
        self.assertIn('default_local_evidence_changed', report['diagnostic']['message'])
        network.assert_not_called()
        self.assertFalse(self.requested.exists())

    def test_evidence_changed_during_download_prevents_success_and_preserves_payload(self):
        def transfer(*args, **kwargs):
            Path(self.reference['path']).write_text('changed during transfer')
            return self.response()
        with patch('dataset_prepare.sources.open_source', side_effect=transfer) as network:
            code, report = self.execute()
        self.assertEqual(code, 1, report)
        self.assertIn('default_local_evidence_changed', report['diagnostic']['message'])
        self.assertNotIn('asset', report)
        self.assertTrue((self.requested / 'source-payload').is_file())
        network.assert_called_once()

    def test_default_content_changed_between_lookup_and_prepare_is_not_published(self):
        gsm8k(self.default)
        prepare = entry.prepare_asset
        def changing_prepare(request, path, attempt):
            (self.default / 'test.jsonl').write_text('{"question":"changed","answer":"#### 5"}\n')
            return prepare(request, path, attempt)
        with patch.object(entry, 'prepare_asset', side_effect=changing_prepare), \
                patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute()
        self.assertEqual(code, 1, report)
        self.assertIn('default_local_identity_changed', report['diagnostic']['message'])
        self.assertNotIn('asset', report)
        network.assert_not_called()

    def test_download_failure_and_cancellation_preserve_resolution_without_retry(self):
        for error in (OSError('synthetic network failure'), KeyboardInterrupt('synthetic cancellation')):
            request = {**self.request, 'path': str(self.root / type(error).__name__)}
            with self.subTest(error=type(error).__name__), patch('dataset_prepare.sources.open_source', side_effect=error) as network:
                code, report = self.execute(request)
            self.assertEqual(code, 1, report)
            self.assertEqual(report['diagnostic']['type'], type(error).__name__)
            snapshot = json.loads(Path(report['evidence']['source_snapshot']).read_text())
            self.assertEqual(snapshot['resolution']['decision'], 'download_requested')
            network.assert_called_once()

    def test_default_declaration_requires_absolute_paths_and_bound_evidence(self):
        invalid = [None, {'path': str(self.default)},
                   {'path': 'relative', 'evidence': self.reference},
                   {'path': str(self.default), 'evidence': {'path': 'relative', 'sha256': '0' * 64}},
                   {'path': str(self.default), 'evidence': {**self.reference, 'sha256': 'invalid'}}]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                normalize_request({**self.request, 'default_local': candidate})
        with self.assertRaises(ValueError):
            normalize_request({**self.request, 'source': 'local'})

    def test_redirected_default_is_rejected_without_download(self):
        actual = self.root / 'actual'; gsm8k(actual)
        self.default.symlink_to(actual, target_is_directory=True)
        with patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute()
        self.assertEqual(code, 1, report)
        self.assertIn('default_local_redirected', report['diagnostic']['message'])
        network.assert_not_called()

    def test_local_reuse_cannot_claim_expected_archive_sha256(self):
        gsm8k(self.default)
        with patch('dataset_prepare.sources.open_source') as network:
            code, report = self.execute({**self.request, 'expected_sha256': '0' * 64})
        self.assertEqual(code, 1, report)
        self.assertIn('expected_sha256', report['diagnostic']['message'])
        network.assert_not_called()

    def test_evidence_output_cannot_overlap_default_or_source_evidence(self):
        for protected in ('default', 'evidence'):
            request = copy.deepcopy(self.request)
            if protected == 'default':
                request['default_local']['path'] = str(self.work)
            else:
                request['default_local']['evidence']['path'] = str(self.work / 'fixture/vllm-dataset-prepare/source.txt')
            with self.subTest(protected=protected), self.assertRaisesRegex(ValueError, '证据'):
                output_parent(normalize_request(request), self.work, 'fixture')
        self.assertFalse(self.work.exists())

    def test_public_plan_accepts_default_without_reading_evidence_or_creating_directories(self):
        Path(self.reference['path']).unlink()
        request_path = self.root / 'request.json'; request_path.write_text(json.dumps(self.request))
        result = subprocess.run([sys.executable, '-B', str(DATA / 'prepare.py'), '--request', str(request_path),
                                 '--work-root', str(self.work), '--run-id', 'fixture', '--plan'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.work.exists())
        self.assertFalse(self.requested.exists())


class MissingCatalogTests(unittest.TestCase):
    def test_missing_path_including_metadata_mode_guides_default_lookup_before_download(self):
        from precision.runtime.evalscope import catalog
        @dataclass
        class Metadata:
            name: str = 'gsm8k'
        adapter = SimpleNamespace(benchmark_meta=Metadata(), subset_list=['default'], eval_split='test')
        modules = {'evalscope.api.registry': SimpleNamespace(get_benchmark=lambda *args: adapter)}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            request = {'model': 'fixture', 'model_path': str(root / 'model'),
                       'dataset': {'name': 'gsm8k', 'path': str(root / 'missing'), 'subset_list': ['default'], 'limit': 15}}
            for metadata_only in (False, True):
                with self.subTest(metadata_only=metadata_only), patch.dict(sys.modules, modules), \
                        patch.object(catalog, 'native_config', side_effect=lambda raw: raw), \
                        patch.object(catalog, 'capability'), patch.object(catalog.importlib.metadata, 'version', return_value='fixture'), \
                        patch.object(catalog, 'load_subsets') as loader:
                    result = catalog.inspect({**request, 'metadata_only': metadata_only}, root)
                self.assertEqual(result['next_action'], 'agent_resolve_official_dataset_source')
                self.assertEqual(result['source_resolution']['download_path'], request['dataset']['path'])
                self.assertTrue(result['source_resolution']['check_default_local_first'])
                loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
