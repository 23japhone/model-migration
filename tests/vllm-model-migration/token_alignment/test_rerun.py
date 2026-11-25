"""单题定向评测在原生模板和 few-shot 渲染后选择样本。"""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
VALIDATOR = ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'
sys.path.insert(0, str(VALIDATOR))
from precision.token_run.native import choose
from precision.token_run.capture import post_pair


class Rows(list):
    def filter(self, predicate):
        return Rows(row for row in self if predicate(row))


class TargetedRerun(unittest.TestCase):
    def test_preserves_native_index_and_checks_rendered_sample(self):
        rows = Rows([SimpleNamespace(id=i, input=f'prompt-{i}') for i in range(3)])
        with patch('precision.token_run.native.sample_identity', side_effect=lambda row:row.input):
            selected = choose({'math':rows}, 'math', 2, 'prompt-2')
            self.assertEqual([row.id for row in selected['math']], [2])
            with self.assertRaises(ValueError):
                choose({'math':rows}, 'math', 2, 'different few-shot')

    def test_extra_request_is_rejected(self):
        with self.assertRaises(ValueError):
            post_pair({'requests':[{'method':'POST'}]*2,'errors':[]}, Path('/tmp'))

    def test_native_protocol_must_match_first_measurement(self):
        from precision.token_run.run import selection
        recorded = {'dataset': 'gsm8k', 'model_identity': 'first'}
        request = {'quality_report': 'quality', 'selection': 'selected',
                   'native_request': {'protocol': {**recorded, 'model_identity': 'other'}}}
        report = {'task_id': 'vllm-precision-compare', 'operation': 'run', 'status': 'completed',
                  'baseline': {'dataset': 'gsm8k'}, 'conclusions': {'precision_target': 'fail', 'evaluation_validity': 'pass'},
                  'scores': [{'subset': 'main', 'metric': 'acc', 'direction': 'higher', 'comparability': 'pass', 'precision_target': 'fail'}],
                  'evidence': {'measurement': 'measurement', 'receipt': 'receipt'}}
        store = {'quality': report, 'selected': {'quality_report': 'quality', 'dataset': 'gsm8k',
                 'cases': [{'subset': 'main', 'sample_id': 0, 'triggers': [{'subset': 'main', 'metric': 'acc',
                 'direction': 'higher', 'selection_basis': 'failed_subset_roster'}]}]}, 'measurement': {
                 'format': 'task-quality-native-v1', 'hardware': 'npu', 'protocol': recorded},
                 'receipt': {'task_id': 'verify-agent-plus', 'status': 'completed',
                 'conclusions': {'evaluation_validity': 'pass'}, 'detailed_report': {'hardware': 'npu'},
                 'evidence': {'quality_evidence': 'measurement'}}}
        with patch('precision.token_run.run.bound', side_effect=store.__getitem__):
            with self.assertRaisesRegex(ValueError, '协议'):
                selection(request)
            request['native_request']['protocol'] = recorded
            self.assertEqual(selection(request)[0], store['selected']['cases'])

    def test_independent_row_survives_another_row_failing(self):
        from precision.token_run import run
        from tempfile import TemporaryDirectory
        rows = [{'subset': 'main', 'sample_id': n} for n in (2, 3)]
        with TemporaryDirectory() as folder, patch.object(
                run, 'capture_case', create=True, side_effect=[ValueError('capture failed'), rows[1]]):
            successful, issues = run.collect_cases(Path(folder), {}, rows, {})
            self.assertEqual(successful, [rows[1]])
            self.assertEqual(issues, [{**rows[0], 'message': 'capture failed'}])

    def test_dataset_total_is_not_a_fallback_roster(self):
        from precision.token_run.run import checked_rows
        trigger = {'metric': 'acc', 'subset': None, 'direction': 'higher', 'selection_basis': 'failed_subset_roster'}
        with self.assertRaises(ValueError):
            checked_rows([{'subset': 'default', 'sample_id': 3, 'triggers': [trigger]}], [trigger])
