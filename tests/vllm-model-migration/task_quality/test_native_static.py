"""NPU 独立测量与复核可读取带旧 GPU 静态参数的请求。"""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = (Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align'
           / 'skills/verify-agent-plus/scripts')
sys.path.insert(0, str(SCRIPTS))

from precision.quality import history, run, runtime


class NativeMeasurementConditions(unittest.TestCase):
    def setUp(self):
        self.protocol = {'subsets': ['main'], 'prompt_config': {}, 'generation_config': {'max_tokens': 32}}
        self.static = {'chat_template_sha256': 'a'*64, 'tokenizer_sha256': 'b'*64,
                       'server_generation': {'overrides': {'--generation-config': 'auto'}, 'model_generation_files': {}}}
        self.request = {'measurement': {}, 'scores': [], 'expected_static_conditions': self.static, 'protocol': self.protocol}

    def test_opaque_service_configuration_does_not_claim_known_template_or_tokenizer(self):
        before = {'generation': {'--config': {'sha256': 'c'*64}}}
        index = {'model_files': {'tokenizer.json': 'b'*64}, 'prompt_budget': self.static}
        measured = runtime.recorded(index, before)
        original = runtime.ordinary({'model_files': index['model_files'], 'server_generation': before['generation'],
                                     'datasets': []})
        for conditions in (measured, original):
            self.assertIsNone(conditions['chat_template_sha256'])
            self.assertIsNone(conditions['server_generation'])
            self.assertIsNone(conditions['tokenizer_sha256'])

    def test_quality_run_reaches_model_with_different_static_conditions(self):
        inputs = {'protected': [], 'identity': {'model_files': {}}}
        index = {'tasks': [], 'prompt_budget': {}}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as mocks:
            for name, value in [('collect', inputs), ('subset_assets', {}), ('check_output', None),
                                ('check_assets', None), ('freeze', index)]:
                mocks.enter_context(patch.object(run, name, return_value=value))
            mocks.enter_context(patch.object(run.service, 'snapshot', return_value={'generation': {}}))
            model = mocks.enter_context(patch.object(run, 'run_native', return_value={'returncode': 1}))
            code, receipt = run.run(self.request, Path(temporary), 'new-run')
            model.assert_called_once()
        self.assertEqual('failed', receipt['status'])
        self.assertIn('原生进程失败', receipt['diagnostic']['message'])

    def test_analyze_accepts_verified_source_with_different_template(self):
        original = {'operation': 'run', 'detailed_report': {}}
        measured = {'scores': [], 'stable_samples': {'main':['q1']}, 'static_conditions': {**self.static, 'chat_template_sha256': 'c'*64}}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as mocks:
            mocks.enter_context(patch.object(history, 'source_run', return_value=original))
            mocks.enter_context(patch.object(history, 'check_output'))
            mocks.enter_context(patch.object(history, 'ordinary', return_value=(self.protocol, measured)))
            model = mocks.enter_context(patch.object(run, 'run_native'))
            source = {'path': 'historical', 'sha256': 'a'*64}
            mocks.enter_context(patch.object(history, 'ref', return_value=source))
            code, receipt = history.analyze({'source': source, 'expected': self.request},
                                            Path(temporary), 'analysis')
            model.assert_not_called()
        self.assertEqual((0, 'completed'), (code, receipt['status']))
        self.assertEqual({'main':['q1']}, receipt['detailed_report']['stable_samples'])
        self.assertEqual('pass', receipt['conclusions']['evaluation_validity'])


if __name__ == '__main__':
    unittest.main()
