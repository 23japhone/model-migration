"""用构造的 GPU runner 输入检查实际 v0.20.2 输出行和 chunked prefill。"""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

VERIFY = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent'
sys.path.insert(0, str(VERIFY / 'scripts'))


class GpuReplay(unittest.TestCase):
    def test_expected_runtime_weights_are_measured_from_worker_path(self):
        from hashlib import sha256
        import json
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from verification.gpu_capture.runtime import runtime_identity
        # 第一轮模型输入库存以文件原件及相对路径形成唯一签名。
        sys.path.insert(0, str(VERIFY.parents[3] / 'scripts'))
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / 'w.bin'
            path.write_bytes(b'input weights')
            files = {'w.bin':sha256(path.read_bytes()).hexdigest()}
            digest = sha256(json.dumps(files, separators=(',',':')).encode()).hexdigest()
            versions = {'msprobe':'1','runtime':'test'}
            runner = SimpleNamespace(model_config=SimpleNamespace(model=str(root)))
            plan = {'model_identity':digest,'runtime_versions':{'gpu':versions}}
            with patch('verification.gpu_capture.runtime.runtime_versions', return_value=versions):
                self.assertEqual(runtime_identity(runner, plan, files), versions)
                path.write_bytes(b'other weights')
                with self.assertRaisesRegex(ValueError,'资产复核'):
                    runtime_identity(runner, plan, files)
                (root/'w2.bin').write_bytes(b'extra')
                with self.assertRaisesRegex(ValueError,'权重文件集合'):
                    runtime_identity(runner, plan, files)

    def test_chunked_prefill_and_requested_decode(self):
        import torch
        from verification.gpu_capture import select_frame
        request = SimpleNamespace(prompt_token_ids=[1, 2, 3], output_token_ids=[], mm_features=[])
        runner = SimpleNamespace(requests={'req': request}, input_batch=SimpleNamespace(req_ids=['req']))
        plan = {'prompt_token_ids': [1, 2, 3], 'prefix_token_ids': [1, 2, 3], 'position': 1}
        def frame(count, start, tokens):
            return select_frame(runner, 'req', plan, count, torch.tensor(tokens),
                                torch.arange(start, start + count), torch.tensor([count - 1]))
        self.assertIsNone(frame(2, 0, [1, 2]))
        self.assertEqual(frame(1, 2, [3])['output_index'], 0)
        request.output_token_ids.append(4)
        self.assertIsNone(frame(1, 3, [4]))
        plan.update(prefix_token_ids=[1, 2, 3, 4], position=2)
        self.assertEqual(frame(1, 3, [4])['positions'], [3])
        with self.assertRaises(ValueError):
            frame(1, 3, [1])
