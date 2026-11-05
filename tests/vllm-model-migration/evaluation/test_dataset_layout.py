"""原始数据到原生评测目录的公开入口回归，不调用模型。"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
DATA = ROOT / 'skills/vllm-migration-prepare/skills/vllm-dataset-prepare/scripts'
PRECISION = ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'
sys.path[:0] = [str(DATA), str(PRECISION), str(ROOT / 'scripts')]


def ceval(root):
    for split in ('dev', 'val', 'test'):
        (root / split).mkdir(parents=True)
        for subject in ('accountant', 'advanced_mathematics'):
            rows = 'id,question,A,B,C,D,answer,explanation\n'
            rows += ''.join(f'{i},question {i},one,two,three,four,A,reason\n' for i in range(20))
            (root / split / f'{subject}_{split}.csv').write_text(rows)


class DatasetLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def prepare(self, request):
        path = self.root / 'request.json'
        path.write_text(json.dumps(request))
        result = subprocess.run([sys.executable, '-B', str(DATA / 'prepare.py'), '--request', str(path),
                                 '--work-root', str(self.root / 'work'), '--run-id', 'fixture'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(Path(json.loads(result.stdout)['manifest']).read_text())

    def test_ceval_nested_root_builds_metadata_and_preserves_raw_bytes(self):
        raw = self.root / 'source' / 'extracted-example'
        ceval(raw)
        before = {p.relative_to(raw).as_posix(): p.read_bytes() for p in raw.rglob('*') if p.is_file()}
        for subject in ('accountant', 'advanced_mathematics'):
            report = self.prepare({'source': 'local', 'recipe': 'ceval', 'path': str(raw.parent),
                                   'layout': 'evalscope', 'split': 'val', 'subjects': [subject],
                                   'selection': {'limit': 15}})
            asset = report['asset']; prepared = Path(asset['path'])
            self.assertFalse(prepared.is_relative_to(raw.parent))
            self.assertEqual(asset['selection']['count'], 15)
            self.assertEqual({r['subject'] for r in asset['selection']['records']}, {subject})
            metadata = (prepared / 'README.md').read_text()
            self.assertIn('config_name: "accountant"', metadata)
            self.assertIn('config_name: "advanced_mathematics"', metadata)
            self.assertIn('val/accountant_val.csv', metadata)
            self.assertEqual({r['path'] for r in asset['files']}, {*before, 'README.md'})
            self.assertEqual(next(r['rows'] for r in asset['files'] if r['path'] == 'README.md'), 0)
            for relative, content in before.items():
                self.assertEqual((prepared / relative).read_bytes(), content)
            self.assertEqual(asset['provenance']['source_root'], str(raw))
        self.assertEqual(before, {p.relative_to(raw).as_posix(): p.read_bytes() for p in raw.rglob('*') if p.is_file()})

    def test_gsm8k_only_copies_main_files_and_tracks_source_identity(self):
        raw = self.root / 'gsm8k'; raw.mkdir()
        content = json.dumps({'question': '1+1?', 'answer': 'two #### 2'}) + '\n'
        for name in ('train', 'test', 'train_socratic', 'test_socratic'):
            (raw / (name + '.jsonl')).write_text(content)
        report = self.prepare({'source': 'local', 'recipe': 'gsm8k', 'path': str(raw),
                               'layout': 'evalscope', 'split': 'test'})
        asset = report['asset']; prepared = Path(asset['path'])
        self.assertEqual(sorted(p.name for p in prepared.iterdir()), ['README.md', 'test.jsonl', 'train.jsonl'])
        self.assertEqual(len(list(raw.iterdir())), 4)
        for row in asset['provenance']['source_files']:
            self.assertEqual((prepared / row['path']).read_bytes(), (raw / row['path']).read_bytes())
        metadata = next(row for row in asset['files'] if row['path'] == 'README.md')
        self.assertEqual((metadata['rows'], metadata['split'], metadata['subject']), (0, None, None))
        self.assertEqual(metadata['sha256'], hashlib.sha256((prepared / 'README.md').read_bytes()).hexdigest())
        self.assertNotEqual(asset['identity'], asset['provenance']['source_identity'])

    def test_gsm8k_generates_default_main_metadata_without_rewriting_source(self):
        raw = self.root / 'gsm8k'; raw.mkdir()
        for split in ('train', 'test'):
            (raw / (split + '.jsonl')).write_text(json.dumps({'question': split, 'answer': '#### 1'}) + '\n')
        original_metadata = b'---\nconfigs:\n- config_name: socratic\n---\n'
        (raw / 'README.md').write_bytes(original_metadata)
        report = self.prepare({'source': 'local', 'recipe': 'gsm8k', 'path': str(raw),
                               'layout': 'evalscope', 'split': 'test'})
        metadata = (Path(report['asset']['path']) / 'README.md').read_text()
        self.assertIn('config_name: main', metadata)
        self.assertIn('default: true', metadata)
        self.assertIn('split: train\n    path: train.jsonl', metadata)
        self.assertIn('split: test\n    path: test.jsonl', metadata)
        self.assertNotIn('socratic', metadata)
        self.assertEqual((raw / 'README.md').read_bytes(), original_metadata)
        self.assertEqual(report['asset']['selection']['count'], 1)

    def test_gsm8k_raw_keeps_original_files_and_metadata_policy(self):
        raw = self.root / 'gsm8k'; raw.mkdir()
        for split in ('train', 'test'):
            (raw / (split + '.jsonl')).write_text(json.dumps({'question': split, 'answer': '#### 1'}) + '\n')
        report = self.prepare({'source': 'local', 'recipe': 'gsm8k', 'path': str(raw), 'split': 'test'})
        self.assertEqual(report['asset']['path'], str(raw))
        self.assertEqual({row['path'] for row in report['asset']['files']}, {'train.jsonl', 'test.jsonl'})
        self.assertFalse((raw / 'README.md').exists())

    def test_gsm8k_metadata_tampering_is_rejected_by_precision_consumer(self):
        from precision.inputs import dataset
        raw = self.root / 'gsm8k'; raw.mkdir()
        for split in ('train', 'test'):
            (raw / (split + '.jsonl')).write_text(json.dumps({'question': split, 'answer': '#### 1'}) + '\n')
        report = self.prepare({'source': 'local', 'recipe': 'gsm8k', 'path': str(raw),
                               'layout': 'evalscope', 'split': 'test'})
        manifest = next((self.root / 'work').rglob('dataset-manifest.json'))
        reference = {'path': str(manifest), 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
        self.assertEqual(dataset(reference)['selection']['count'], 1)
        (Path(report['asset']['path']) / 'README.md').write_text('changed\n')
        with self.assertRaises(ValueError):
            dataset(reference)

    def test_default_raw_layout_remains_in_place(self):
        raw = self.root / 'ceval'; ceval(raw)
        report = self.prepare({'source': 'local', 'recipe': 'ceval', 'path': str(raw), 'split': 'val'})
        self.assertEqual(report['asset']['path'], str(raw))
        self.assertFalse((raw / 'README.md').exists())

    def test_ceval_metadata_fixes_csv_types_and_uses_evaluation_split_columns(self):
        raw = self.root / 'ceval'; ceval(raw)
        for path in (raw / 'val').glob('*.csv'):
            path.write_text('id,question,A,B,C,D,answer\n0,question,one,two,three,four,A\n')
        report = self.prepare({'source': 'local', 'recipe': 'ceval', 'path': str(raw),
                               'layout': 'evalscope', 'split': 'val', 'subjects': ['accountant']})
        text = (Path(report['asset']['path']) / 'README.md').read_text()
        self.assertIn('keep_default_na: false', text)
        self.assertIn('name: "id"\n    dtype: string', text)
        self.assertNotIn('name: "explanation"', text)
        self.assertNotIn('path: "test/', text)
        self.assertTrue((Path(report['asset']['path']) / 'test/accountant_test.csv').is_file())

    def test_unsupported_layout_recipe_is_rejected_before_effects(self):
        from dataset_prepare.request import normalize_request
        with self.assertRaisesRegex(ValueError, 'layout|布局'):
            normalize_request({'source': 'local', 'recipe': 'generic', 'path': '/absent',
                               'split': 'test', 'format': 'jsonl', 'layout': 'evalscope'})


class DatasetBindingTests(unittest.TestCase):
    def test_omitted_path_binds_without_mutating_source_and_conflict_explains_both_paths(self):
        from precision.runtime.evalscope.data import bind_config
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); data = root / 'extracted'; data.mkdir()
            asset = {'path': str(data)}
            raw = {'dataset_args': {'ceval': {'subset_list': ['accountant']}}}
            before = copy.deepcopy(raw)
            result = bind_config(raw, asset)
            self.assertEqual(raw, before)
            self.assertEqual(result['dataset_args']['ceval']['local_path'], str(data))
            self.assertEqual(bind_config(result, asset), result)
            raw['dataset_args']['ceval']['local_path'] = str(root)
            with self.assertRaisesRegex(ValueError, 'dataset_path_conflict') as caught:
                bind_config(raw, asset)
            self.assertIn(str(root), str(caught.exception))
            self.assertIn(str(data), str(caught.exception))
            self.assertEqual(raw['dataset_args']['ceval']['local_path'], str(root))

    def test_customer_config_uses_prepared_root_not_catalog_path(self):
        from customer_workflow.compile.accuracy import task_config
        task = {'model': {'model_name': 'model'}}
        job = {'dataset': {'name': 'ceval', 'limit': 15}, 'subset': {'name': 'accountant', 'split': 'val'},
               'catalog': {'local_path': '/old-parent'}, 'prepared': {'asset': {'path': '/prepared-root'}}}
        config = task_config(task, job, 'http://127.0.0.1:8000', False)
        self.assertEqual(config['dataset_args']['ceval']['local_path'], '/prepared-root')


if __name__ == '__main__':
    unittest.main()
