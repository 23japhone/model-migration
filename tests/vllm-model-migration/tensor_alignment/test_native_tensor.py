"""原生重放必须使用候选题实际的两端前文。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills'
sys.path.insert(0, str(SKILLS / 'vllm-precision-align/skills/verify-agent-plus/scripts'))


class NativeTensor(unittest.TestCase):
    def test_real_prefix_for_each_side_and_output(self):
        from precision.token_run.tensor import check_prediction
        plan = {'case': {'sample_id': 'q', 'subset': 'a'}, 'position': 2,
                'phase': 'decode', 'prefix_token_ids': [2, 5], 'prompt_token_ids': [2]}
        row = {'sample_id': 'q', 'subset': 'a', 'prompt_token_ids': [2], 'output_token_ids': [5, 3]}
        with patch('precision.token_run.tensor.bound', return_value=row):
            check_prediction(plan, {})
            for key, value in [('prefix_token_ids', [2, 3]), ('position', 3), ('phase', 'prefill')]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    check_prediction({**plan, key: value}, {})
