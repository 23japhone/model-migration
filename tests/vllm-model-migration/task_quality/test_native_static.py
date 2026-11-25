"""自动搜索的 GPU 基线须可在推理前与 NPU 静态条件核对。"""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = (Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align'
           / 'skills/verify-agent-plus/scripts')
sys.path.insert(0, str(SCRIPTS))

from precision.quality import history, run, runtime, static_gate


class AutomaticBaselineGate(unittest.TestCase):
    def setUp(self):
        self.protocol = {'subsets': ['main'], 'prompt_config': {}, 'generation_config': {'max_tokens': 32}}
        self.static = {'chat_template_sha256': 'a'*64, 'tokenizer_sha256': 'b'*64,
                       'server_generation': {'overrides': {'--generation-config': 'auto'}, 'model_generation_files': {}}}
        self.request = {'measurement': {}, 'scores': [], 'expected_static_conditions': self.static, 'protocol': self.protocol}

    def test_optional_gate_requires_all_three_verified_fields(self):
        static_gate.check({}, {})
        static_gate.check(self.request, self.static)
        for key in self.static:
            current = self.static.copy()
            current.pop(key)
            for request, value in ((self.request, current), ({**self.request, 'expected_static_conditions': current}, self.static)):
                with self.subTest(key=key), self.assertRaisesRegex(static_gate.Mismatch, key):
                    static_gate.check(request, value)

    def test_opaque_service_configuration_does_not_claim_known_template_or_tokenizer(self):
        before = {'generation': {'--config': {'sha256': 'c'*64}}}
        index = {'model_files': {'tokenizer.json': 'b'*64}, 'prompt_budget': self.static}
        measured = runtime.recorded(index, before)
        original = runtime.ordinary({'model_files': index['model_files'], 'server_generation': before['generation'],
                                     'datasets': []})
        for conditions in (measured, original):
            with self.assertRaisesRegex(static_gate.Mismatch, 'chat_template_sha256'):
                static_gate.check(self.request, conditions)
            self.assertIsNone(conditions['server_generation'])
            self.assertIsNone(conditions['tokenizer_sha256'])

    def test_unresolved_service_budget_and_random_examples_need_source_evidence(self):
        self.protocol['generation_config'] = {}
        with self.assertRaisesRegex(static_gate.Mismatch, 'effective_max_tokens'):
            static_gate.check(self.request, self.static)
        self.static['effective_max_tokens'] = 16
        self.protocol['prompt_config'] = {'few_shot_random': True, 'few_shot_num': 1}
        with self.assertRaisesRegex(static_gate.Mismatch, 'reader_sha256_by_subset'):
            static_gate.check(self.request, self.static)
        self.static['reader_sha256_by_subset'] = {'main': 'c'*64}
        static_gate.check(self.request, self.static)
        current = {**self.static, 'reader_sha256_by_subset': {'main': 'd'*64}}
        with self.assertRaisesRegex(static_gate.Mismatch, 'reader_sha256_by_subset'):
            static_gate.check(self.request, current)

    def test_quality_run_blocks_before_model_request_with_receipt(self):
        inputs = {'protected': [], 'identity': {'model_files': {}}}
        index = {'tasks': [], 'prompt_budget': {}}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as mocks:
            for name, value in [('collect', inputs), ('subset_assets', {}), ('check_output', None),
                                ('check_assets', None), ('freeze', index)]:
                mocks.enter_context(patch.object(run, name, return_value=value))
            mocks.enter_context(patch.object(run.service, 'snapshot', return_value={'generation': {}}))
            model = mocks.enter_context(patch.object(run, 'run_native'))
            code, receipt = run.run(self.request, Path(temporary), 'new-run')
            model.assert_not_called()
        self.assert_blocked(code, receipt)

    def test_analyze_rejects_original_with_different_template(self):
        original = {'operation': 'run', 'detailed_report': {}}
        measured = {'scores': [], 'static_conditions': {**self.static, 'chat_template_sha256': 'c'*64}}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as mocks:
            mocks.enter_context(patch.object(history, 'source_run', return_value=original))
            mocks.enter_context(patch.object(history, 'check_output'))
            mocks.enter_context(patch.object(history, 'ordinary', return_value=(self.protocol, measured)))
            model = mocks.enter_context(patch.object(run, 'run_native'))
            code, receipt = history.analyze({'source': {'path': 'historical'}, 'expected': self.request},
                                            Path(temporary), 'analysis')
            model.assert_not_called()
        self.assert_blocked(code, receipt)

    def assert_blocked(self, code, receipt):
        self.assertEqual((code, receipt['status']), (3, 'blocked'))
        self.assertEqual(receipt['diagnostic']['code'], 'quality_static_mismatch')
        self.assertEqual(receipt['detailed_report']['formal_requests'], 0)
        self.assertEqual(receipt['conclusions']['evaluation_validity'], 'not_evaluated')


if __name__ == '__main__':
    unittest.main()
