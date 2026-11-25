"""原生总分、复核统计和已绑定样本身份。"""
import sys
import unittest
from pathlib import Path

SCRIPTS = (Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align'
           / 'skills/verify-agent-plus/scripts')
sys.path.insert(0, str(SCRIPTS))
from precision.quality.projection import scores


class NativeProjection(unittest.TestCase):
    def setUp(self):
        self.counts = {'a': 2, 'b': 1}
        self.definition = {'metric': 'accuracy', 'sample_metric': 'acc', 'unit': 'ratio',
                           'selector': {'name': 'accuracy', 'aggregation': 'mean', 'dimensions': {}},
                           'direction': 'higher', 'sample_kind': 'binary'}
        metric = {'identity': self.definition['selector'], 'num': 3, 'score': 0.6667,
                  'semantics': {'kind': 'quality', 'raw_unit': 'ratio', 'direction': 'higher_is_better'},
                  'categories': [{'subsets': [{'name': 'a','num': 2,'score': 0.5},
                                               {'name': 'b','num': 1,'score': 1.0}]}]}
        self.report = {'metrics': [metric]}
        self.reviews = {'a': [('case-1', {'acc': False}), ('case-4', {'acc': True})],
                        'b': [('questions.jsonl#0', {'acc': True})]}

    def select(self, subset):
        return scores(self.report, self.reviews, [{**self.definition, 'subset': subset}])[0]

    def test_native_total_and_subset_from_same_report(self):
        self.assertEqual(self.select(None)['value'], 0.6667)
        self.assertEqual(self.select('b')['value'], 1.0)
        self.assertEqual([row['sample_id'] for row in self.select(None)['samples']],
                         ['case-1','case-4','questions.jsonl#0'])

    def test_absent_aggregate_does_not_discard_a_native_subset(self):
        self.report['metrics'][0]['score'] = None
        self.assertEqual(0.5, self.select('a')['value'])
        self.assertIn('error', self.select(None))

    def test_wrong_native_denominator_is_rejected(self):
        self.report['metrics'][0]['num'] = 2
        self.assertIsNone(self.select(None)['value'])
        self.assertEqual(1.0, self.select('b')['value'])

    def test_absent_review_field_is_not_scored_as_zero(self):
        self.reviews['a'][0] = ('case-1', {'other': 0})
        result = self.select(None)
        self.assertEqual(0.6667, result['value'])
        self.assertEqual('case-1', result['sample_errors'][0]['sample_id'])
        self.assertEqual(2, len(result['samples']))


if __name__ == '__main__':
    unittest.main()
