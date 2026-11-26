"""验证现场 CPU 前向选择精度的顺序与停止条件。"""
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = (Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills'
           / 'vllm-precision-align/skills/vllm-precision-compare/scripts')


def crash_worker(channel, *args):
    channel.send([{'dtype': 'float32', 'status': 'running'}])
    os._exit(23)


class CpuPrecision(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        from comparison import token_cpu
        self.cpu = token_cpu
        self.addCleanup(patch.stopall)
        patch.object(token_cpu, 'verify_files', return_value=Path('/local/weights')).start()
        try:
            from transformers import GPT2Config
            import torch
        except ImportError:
            self.skipTest('需要 torch 和 transformers')
        self.torch = torch
        patch('transformers.AutoConfig.from_pretrained', return_value=GPT2Config()).start()
        self.spec = {'root': '/local/weights', 'files': {'config.json': '0' * 64},
                     'weight_files': ['model.safetensors'], 'model_identity': 'a' * 64}
        self.prefix = [0, 1, 1]
        self.raw = ([Decimal(1), Decimal(0)], None)

    def test_falls_back_only_on_unsupported_precision_and_preserves_prefix(self):
        def kernel(root, config, spec, prefix, dtype):
            self.assertEqual(prefix, self.prefix)
            if dtype != self.torch.bfloat16:
                raise RuntimeError(f'"linear" not implemented for {dtype}')
            return self.raw, {'output_dtype': 'bfloat16'}
        self.spec['dtype'] = 'float64'
        with patch.object(self.cpu, 'at_dtype', side_effect=kernel):
            attempts = []
            vectors, info = self.cpu.forward(self.spec, self.prefix, attempts)
        self.assertEqual(vectors, self.raw)
        self.assertEqual(info['dtype'], 'bfloat16')
        self.assertEqual([row['dtype'] for row in attempts], ['float64', 'float32', 'bfloat16'])
        self.assertEqual([row['status'] for row in attempts], ['failed', 'failed', 'completed'])
        self.assertIn('linear', attempts[0]['error'])

    def test_missing_weights_is_fatal_without_trying_lower_dtypes(self):
        with patch.object(self.cpu, 'at_dtype', side_effect=FileNotFoundError('weight shard missing')):
            attempts = []
            with self.assertRaisesRegex(FileNotFoundError, 'shard missing'):
                self.cpu.forward(self.spec, self.prefix, attempts)
        self.assertEqual([row['dtype'] for row in attempts], ['float32'])

    def test_visits_available_fp8_after_other_dtypes_fail(self):
        with patch.object(self.cpu, 'at_dtype', side_effect=RuntimeError('"addmm" not implemented for dtype')):
            attempts = []
            with self.assertRaisesRegex(RuntimeError, 'addmm'):
                self.cpu.forward(self.spec, self.prefix, attempts)
        tried = [row['dtype'] for row in attempts]
        self.assertEqual(tried[:3], ['float32', 'bfloat16', 'float16'])
        if hasattr(self.torch, 'float8_e4m3fn'):
            self.assertIn('float8_e4m3fn', tried[3:])
        self.assertTrue(all(row['error'] for row in attempts))

    def test_each_new_comparison_passes_only_the_accepted_prefix(self):
        from types import SimpleNamespace
        from comparison.token_reference import verify_cpu
        from comparison import token_reference
        gpu = {'prompt_token_ids': [0, 1], 'output_token_ids': [1, 0, 2]}
        npu = {**gpu, 'output_token_ids': [1, 1, 0]}
        sources = {k: SimpleNamespace(value=v, ref=k) for k, v in [('gpu', gpu), ('npu', npu)]}
        result = {'result': 'mismatch', 'first_anomalous_token': 2, 'evidence_kind': {}}
        req = {'cpu': 'old-file', 'cpu_model': self.spec}
        with patch.object(token_reference, 'run_worker', return_value={'status': 'completed'}) as call:
            for _ in range(2):
                verify_cpu(req, sources, result, {'raw_logits': 0, 'processed_scores': 0})
                self.assertEqual(call.call_args.args[2], [0, 1, 1])
        self.assertEqual(call.call_count, 2)

    def test_native_worker_exit_keeps_confirmed_mismatch(self):
        from types import SimpleNamespace
        from comparison import token_reference
        report = {'result': 'mismatch', 'first_anomalous_token': 1, 'evidence_kind': {}}
        sample = SimpleNamespace(ref={}, value={'prompt_token_ids': [1], 'output_token_ids': [0]})
        with patch.object(token_reference, 'cpu_worker', crash_worker):
            check = token_reference.verify_cpu({'cpu_model': self.spec}, {'gpu': sample, 'npu': sample}, report, {})
        self.assertEqual(report['result'], 'mismatch')
        self.assertEqual(check['status'], 'unavailable')
        self.assertIn('23', check['reason'])
        self.assertEqual(check['attempts'][0]['status'], 'failed')
        self.assertEqual(check['attempts'][0]['dtype'], 'float32')
        self.assertIsNone(check['gpu'])
