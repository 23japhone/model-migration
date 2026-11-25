import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from coordination.runtime import sources


class ResolutionTests(unittest.TestCase):
    def test_stable_tool_and_hub_snapshot_are_frozen_once(self):
        from customer_workflow.quality.resolution import resolve
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            urls = {'https://pypi.org/pypi/evalscope/json': {'info': {'name': 'evalscope'},
                    'releases': {'1.10.0': [{'yanked': False}], '2.0rc1': [{'yanked': False}],
                                 '2.0': [{'yanked': True}], '1.9': [{'yanked': False}]}},
                    'https://huggingface.co/api/datasets/org/bench/refs': {
                        'tags': [], 'branches': [{'name': 'main', 'targetCommit': 'b' * 40},
                                                  {'name': 'release', 'targetCommit': 'a' * 40}]},
                    'https://huggingface.co/api/datasets/org/bench': {'sha': 'a' * 40}}
            refs = {}
            for index, (url, body) in enumerate(urls.items()):
                path = root / str(index)
                path.write_text(json.dumps({'url': url, 'body': body}))
                refs[url] = sources.ref(path)
            config = {'repository': 'https://huggingface.co/datasets/org/bench', 'responses': refs}
            frozen = resolve({'dataset': 'bench', 'evaluator': 'evalscope'}, config, root / 'quality')
            self.assertEqual(frozen['evaluator_version'], '1.10.0')
            self.assertEqual(frozen['dataset_revision'], 'a' * 40)
            self.assertEqual(frozen['dataset_ref'], 'release')
            again = resolve({'dataset': 'bench', 'evaluator': 'evalscope'}, {**config, 'responses': {}}, root / 'quality')
            self.assertEqual(frozen, again)
