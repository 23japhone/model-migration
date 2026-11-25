"""通过 CPU 构造的同步 runner 回放 GPU 接口，使用真实 msProbe 钩子。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class GpuHooks(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('msprobe'), '当前解释器未加载可选 msProbe')
    def test_target_decode_only_through_sampler(self):
        verify = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py'))
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(verify.parent), *sys.path])}
        proc = subprocess.run([sys.executable, '-B', __file__, 'replay'], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


def replay():
    from hashlib import sha256
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace as Obj
    from unittest.mock import patch
    import torch
    from verification.gpu_capture import capture_request

    model = torch.nn.Sequential(torch.nn.Linear(2, 2, bias=False)).eval()
    with torch.no_grad():
        model[0].weight.copy_(torch.eye(2))
    req = Obj(prompt_token_ids=[0, 1], output_token_ids=[], mm_features=[])
    runner = Obj(device=torch.device('cpu'), requests={'q': req}, input_batch=Obj(req_ids=['q'], sampling_metadata=Obj(all_greedy=True)))
    runner.get_model = lambda: model
    runner.sampler = Obj(greedy_sample=lambda logits: logits.argmax(dim=-1))
    runner._prepare_inputs = lambda schedule, counts: (torch.tensor([0]), None)
    runner._model_forward = lambda **kwargs: model(torch.tensor([[1., 2.]]))

    def execute(scheduler_output):
        runner._prepare_inputs(scheduler_output, [1])
        step = len(req.output_token_ids)
        tokens, positions = (torch.tensor([1]), torch.tensor([1+step]))
        runner.execute_model_state = Obj(logits=runner._model_forward(input_ids=tokens, positions=positions))

    def sample():
        candidate = runner.sampler.greedy_sample(runner.execute_model_state.logits).item()
        req.output_token_ids.append(candidate)
        return Obj(sampled_token_ids=[[candidate]], req_id_to_index={'q': 0})

    runner.execute_model, runner.sample_tokens = execute, sample
    side = {'owner': '0', 'name': 'Functional.linear'}
    call = {'id': 'lm_head.linear', 'kind': 'api', 'occurrence': 0, 'gpu': side, 'npu': side,
            'slots': [{'id': 'y', 'role': 'output', 'gpu': ['output', 0], 'npu': ['output', 0]}]}
    case = {'subset': 'math', 'sample_id': 'q'}
    identity = sha256(b'constructed weights').hexdigest()
    plan = {'format': 'tensor-plan-v1', 'position': 2, 'prefix_token_ids': [0, 1, 1], 'prompt_token_ids': [0, 1],
            'case': case, 'model_identity': identity, 'calls': [call]}
    with TemporaryDirectory() as tmp, torch.no_grad():
        root = Path(tmp).resolve()
        path = root / 'plan.json'
        path.write_text(json.dumps(plan))
        ref = {'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()}
        # 仅替换 CUDA 设备门禁。其余接口在真实 msProbe 和 torch 上运行。
        with patch('verification.gpu_capture.check_runner'), patch('verification.gpu_capture.runtime_identity', return_value={}), capture_request(
                runner, ref, root/'collected', case=case, trace_id='trace', internal_id='q', model_files={}) as result:
            for step in range(3):
                runner.execute_model(scheduler_output=Obj(num_scheduled_tokens={'q': 1}))
                runner.sample_tokens()
                assert len(list(result.parent.rglob('*.pt'))) == (0 if step == 0 else 1)
        bundle = json.loads(result.read_text())
        assert bundle['status'] == 'complete' and bundle['evidence_kind'] == 'synthetic'
        assert bundle['prediction']['token_id'] == 1
        assert len(list(result.parent.rglob('*.bin'))) == 2
        torch.testing.assert_close(torch.load(result.parent/bundle['tensors'][0]['path'], weights_only=True),
                                   torch.tensor([[1., 2.]]))


if __name__ == '__main__' and sys.argv[1:] == ['replay']:
    replay()
