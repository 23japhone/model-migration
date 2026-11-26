"""批次共享原件、成员切片和物理槽的独立核验。"""

import sys
from pathlib import Path
import unittest

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))


class JointContract(unittest.TestCase):
    def test_joint_profile_and_budget(self):
        from verification.runtime.capture.session import validate

        request = dict(
            operation='arm',
            profile='batch_joint',
            source={'path': '/tmp/session', 'sha256': 'a' * 64},
            requests=[dict(trace_id=x, sample_key=x, native_id=i) for i, x in enumerate('AB')],
        )
        validate(request)
        validate({**request, 'cache_capture': dict(mode='full_request', max_request_snapshot_bytes=100)})
        with self.assertRaises(ValueError):
            validate({**request, 'profile': 'batch_identity', 'cache_capture': {}})

    def test_older_identity_only_worker_cannot_arm_numeric_capture(self):
        import tempfile
        from verification.runtime.capture.batch_identity.session import arm
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'batch_joint'):
                arm(dict(profile='batch_joint', requests=[]), Path(directory), dict(state='idle', sealed=[]),
                    dict(batch_identity=True))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_dense_projection_requires_the_observed_parent_and_linear_io(self):
        from verification.runtime.capture.batch_joint.native import member_view

        weight = dict(argument=['input_args', 1], shape=[4, 2], dtype='torch.bfloat16')
        activation = dict(argument=['input_args', 0], shape=[3, 2], dtype='torch.bfloat16')
        output = dict(argument=['output', 0], shape=[3, 4], dtype='torch.bfloat16')
        call = dict(api='Functional.linear', layer='block', module=None,
                    parent='Module.block.self_attn.qkv_proj.AscendQKVParallelLinear.forward.0',
                    tensors=dict(input_args=[activation, weight], output=[output]))
        row = dict(token_range=[1, 3])
        self.assertEqual(member_view(call, output, row, {'block': []}, 3)['range'], [1, 3])
        self.assertEqual(member_view(call, weight, row, {'block': []}, 3)['scope'], 'shared_parameter')
        call['parent'] = 'Module.block.experts.Dense.forward.0'
        self.assertEqual(member_view(call, output, row, {'block': []}, 3)['scope'], 'batch_only')

    def test_same_shape_is_not_proof_of_token_axis(self):
        from verification.runtime.capture.batch_joint.native import member_view

        row = dict(token_range=[1, 3], batch_row=1, logits_index=2)
        tensor = dict(argument=['input_args', 0], shape=[3, 3], dtype='torch.float32')
        call = dict(module=None, api='Functional.mystery', call='opaque')
        self.assertEqual(member_view(call, tensor, row, {}, 3)['scope'], 'batch_only')
        call.update(module='block', layer='block')
        self.assertEqual(member_view(call, tensor, row, {'block': ['hidden_states']}, 3)['range'], [1, 3])
