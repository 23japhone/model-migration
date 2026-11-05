"""有效环境的文件来源、篡改拒绝和三JSON真实生成器回归。"""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from device_contract import bound, effective_runtime, ref, RT
from devices.fixtures import write


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        from test_container_registration_continuation import ContainerRegistrationContinuationTests
        self.case = ContainerRegistrationContinuationTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.handoff = self.case.handoff
        self.original = bound(self.handoff['declared_request'])

    def test_environment_is_derived_without_rewriting_request(self):
        before = copy.deepcopy(self.original)
        runtime = effective_runtime(self.handoff, self.original)
        self.assertEqual(runtime['env'][RT], '0,1')
        self.assertNotIn(RT, self.original['runtime']['env'])
        self.assertEqual(self.original, before)

    def test_changed_mapping_bytes_rejected(self):
        Path(self.handoff['device_mapping']['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, '摘要'):
            effective_runtime(self.handoff, self.original)

    def test_changed_container_identity_rejected(self):
        handoff = copy.deepcopy(self.handoff)
        handoff['resource']['identity']['started_at'] = 'restarted'
        with self.assertRaisesRegex(ValueError, '载体'):
            effective_runtime(handoff, self.original)

    def test_extra_environment_change_rejected_even_with_new_hash(self):
        handoff = copy.deepcopy(self.handoff)
        value = bound(handoff['effective_runtime'])
        value['runtime']['env']['EXTRA_OPTION'] = 'changed'
        handoff['effective_runtime'] = write(self.case.base / 'changed.json', value)
        with self.assertRaisesRegex(ValueError, '非设备'):
            effective_runtime(handoff, self.original)

    def test_old_handoff_is_not_fabricated_as_verified(self):
        handoff = copy.deepcopy(self.handoff)
        del handoff['device_mapping']
        with self.assertRaisesRegex(ValueError, '重新'):
            effective_runtime(handoff)

    def test_export_uses_effective_runtime(self):
        from tuning_handoff.generation import generate
        entry, _ = self.case.make_entry()
        result = generate(entry, {'max_model_len': 9000, 'max_num_seqs': 4,
                                 'kv_token_capacity': 9000, 'runtime_metrics_available': False})
        self.assertEqual(result['deployment']['env_variables'][RT], '0,1')
        self.assertEqual(result['deployment']['env_variables']['ASCEND_VISIBLE_DEVICES'], '4,5')
        self.assertNotIn(RT, bound(self.handoff['declared_request'])['runtime']['env'])


if __name__ == '__main__':
    unittest.main()
