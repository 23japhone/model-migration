import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from coordination.runtime import sources
from customer_workflow.quality.assets import load_source
from customer_workflow.quality.resolution import resolve


def save(path, value):
    path.write_text(json.dumps(value))
    return sources.ref(path)


class KnownSource(unittest.TestCase):
    def test_broken_official_archive_is_a_recoverable_preparation_failure(self):
        from customer_workflow.quality.snapshot import archive_digests
        with self.assertRaisesRegex(ValueError, 'ZIP'):
            archive_digests(b'invalid bytes', {'dataset.csv': 'data/dataset.csv'})

    def test_ceval_shared_asset_is_derived_from_official_full_snapshot(self):
        from customer_workflow.quality.upstream import bind_builtin
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp).resolve()
            source = load_source({}, 'ceval')
            url = source['repository']
            revision = 'a' * 40
            refs = 'ref: refs/heads/master\tHEAD\n' + revision + '\tHEAD\n'
            source['responses'] = {url + '.git': save(d / 'captured.json', {'url': url + '.git', 'body': refs})}
            selection = resolve({'dataset': 'ceval', 'evaluator_version': '2.0', 'evaluator': 'evalscope'}, source, d / 'run')
            contents = io.BytesIO()
            with zipfile.ZipFile(contents, 'w') as data:
                for split in ('dev', 'val', 'test'):
                    data.writestr(f'{split}/demo_{split}.csv', 'id,question,A,B,C,D,answer\n1,foo,0,1,2,3,A\n')
            zipfile_path = d / 'original.zip'
            zipfile_path.write_bytes(contents.getvalue())
            immutable = url + '/resolve/' + revision + '/ceval-exam.zip'
            source['content_sources'] = {immutable: save(d / 'raw.json', {'url': immutable, 'payload': sources.ref(zipfile_path)})}
            source = bind_builtin('ceval', source, selection, d / 'run')
            binding = sources.bound(source['snapshot_binding'])
            self.assertEqual(len(binding['files']), 3)
            self.assertEqual(binding['revision'], revision)
            self.assertTrue((Path(source['preparation']['path']) / 'val/demo_val.csv').is_file())
