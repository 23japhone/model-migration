"""客户模型名称贯通，以及绑定真实本地资产的身份复核门禁。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'skills/vllm-model-migration'
sys.path.insert(0, str(SKILL / 'scripts'))
sys.path.insert(0, str(SKILL / 'skills/vllm-migration-prepare/skills/vllm-model-prepare/scripts'))


def task():
    from test_customer_contract import customer
    return customer()


class ModelNameTests(unittest.TestCase):
    def test_model_name_normalizes_without_mutating_input(self):
        from customer_workflow.contract import normalize
        from test_customer_contract import customer
        value = task()
        original = copy.deepcopy(value)
        normalized = normalize(value)
        self.assertEqual(normalized['model']['model_name'], 'my-model')
        self.assertNotIn('served_model_name', normalized['model'])
        self.assertEqual(normalize(customer()), normalized)
        self.assertEqual(value, original)

    def test_old_name_rejected_even_when_equal_and_model_name_may_default(self):
        from customer_workflow.contract import normalize
        value = task()
        value['model']['served_model_name'] = 'my-model'
        with self.assertRaisesRegex(ValueError, '未知字段'):
            normalize(value)
        del value['model']['model_name']
        with self.assertRaises(ValueError):
            normalize(value)
        for invalid in ('', '-1', True):
            value = task()
            value['model']['model_name'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize(value)
        value = task()
        del value['model']['model_name']
        self.assertEqual(normalize(value)['model']['model_name'], -1)

    def test_prompt_names_share_one_conflict_check(self):
        from customer_workflow.intent import conflicts
        from test_customer_contract import customer
        self.assertEqual(conflicts(customer(), {'model.model_name': 'my-model'}), [])
        with self.assertRaisesRegex(ValueError, '未知字段'):
            conflicts(task(), {'model.served_model_name': 'my-model'})
        self.assertEqual(conflicts(customer(), {'model.model_name': 'other'}), ['model.model_name'])
        with self.assertRaisesRegex(ValueError, '未知字段'):
            conflicts(task(), {'model.model_name': 'my-model', 'model.served_model_name': 'other'})

    def test_name_flows_to_launch_functional_and_accuracy_requests(self):
        from customer_workflow.compile.deployment import command, cases
        from customer_workflow.compile.accuracy import task_config
        value = task()
        recipe = {'runtime_model_path': '/runtime/weights', 'runtime': {'python': '/bin/python'},
                  'dtype': 'bfloat16', 'max_num_seqs': 2}
        argv = command(value, {}, recipe, {'devices': [0], 'tensor_parallel_size': 1, 'memory_fraction': .8}, 8000, 9000)
        self.assertEqual(argv[argv.index('--served-model-name') + 1], 'my-model')
        self.assertEqual({row['payload']['model'] for row in cases(value['model']['model_name'], {'prompt': 'native', 'prompt_tokens': 1, 'chat_template_kwargs': {}})}, {'my-model'})
        job = {'dataset': {'name': 'ceval', 'limit': 15}, 'subset': {'name': 'accountant', 'split': 'val'},
               'catalog': {'local_path': '/data/ceval'}, 'prepared': {'asset': {'path': '/data/prepared'}}}
        self.assertEqual(task_config(value, job, 'http://127.0.0.1:8000', False)['model'], 'my-model')

    def test_public_plan_records_new_model_name_without_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps(task()))
            result = subprocess.run([sys.executable, '-B', str(SKILL / 'scripts/migrate.py'),
                '--task', str(source), '--work-root', str(root / 'runs'), '--run-id', 'name', '--plan'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['inputs']['model']['model_name'], 'my-model')
            self.assertFalse((root / 'runs').exists())

    def test_legacy_resume_is_rejected_without_rewriting_history(self):
        from customer_workflow.contract import normalize
        from customer_workflow.state import Session
        from test_customer_contract import customer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'task.json'
            source.write_text(json.dumps(customer()))
            normalized = normalize(customer())
            first = Session(root / 'runs', source, copy.deepcopy(normalized), 'legacy')
            first.value['inputs']['model']['served_model_name'] = first.value['inputs']['model'].pop('model_name')
            first.save()
            original = first.path.read_bytes()
            with self.assertRaisesRegex(ValueError, '同一客户输入'):
                Session(root / 'runs', source, normalized, 'legacy', first.path)
            self.assertEqual(first.path.read_bytes(), original)


class ModelIdentityTests(unittest.TestCase):
    def setUp(self):
        from model_prepare.customer import resolve
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.model = self.root / 'weights'
        self.model.mkdir()
        self.write('weights/config.json', {'model_type': 'synthetic', 'architectures': ['SyntheticModel']})
        self.write('weights/tokenizer.json', {'model': {'type': 'WordLevel', 'vocab': {'test': 0}}})
        self.write('weights/tokenizer_config.json', {'chat_template': '{{ messages }}'})
        (self.model / 'pytorch_model.bin').write_bytes(b'synthetic weights, never deserialized')
        self.task = task()
        self.task['model']['path'] = str(self.model)
        self.remote = Mock(side_effect=AssertionError('完整本地资产不能触发下载或自动查源'))
        self.resolution = resolve(self.task['model'], remote_lookup=self.remote)
        self.observations = {'model_resolution': self.resolution}

    def write(self, path, value):
        from coordination.runtime.sources import ref
        path = self.root / path
        path.write_text(json.dumps(value))
        return ref(path)

    def review(self, conclusion='match', **changes):
        evidence = self.write('provenance.json', {'repository': 'org/model', 'revision': 'synthetic',
                                                'files': self.resolution['asset']['files']})
        value = {'repository': 'org/model', 'path': str(self.model),
                 'identity': self.resolution['asset']['identity'], 'conclusion': conclusion,
                 'reason': '合成测试：根据来源记录核对本地文件清单', 'evidence': [evidence], **changes}
        self.observations['model_identity_review'] = self.write('review.json', value)
        return value

    def test_resolver_marks_explicit_repository_for_review(self):
        self.assertEqual(self.resolution['resolution'], 'local_identity_review_required')
        self.assertEqual(self.resolution['request']['source'], 'local')
        self.assertEqual(self.resolution['request']['expected_identity'], self.resolution['asset']['identity'])
        self.remote.assert_not_called()

    def test_missing_review_blocks_before_preparation_effects_even_on_resume(self):
        from customer_workflow.runner import prepare
        session = Mock()
        session.ready.return_value = {'status': 'completed'}
        with patch('customer_workflow.runner.execute') as execute:
            with self.assertRaisesRegex(ValueError, '身份复核'):
                prepare(session, self.task, self.observations, self.root / 'runs')
        session.ready.assert_not_called()
        execute.assert_not_called()

    def test_match_is_bound_to_repository_path_and_actual_content(self):
        from customer_workflow.model_identity import verify
        self.review()
        result = verify(self.task, self.observations)
        self.assertEqual(result['conclusion'], 'match')
        for change in ({'repository': 'different/model'}, {'path': '/other/weights'},
                       {'identity': {'format': 'vllm-model-file-list-v1', 'sha256': '0' * 64}}):
            self.review(**change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify(self.task, self.observations)

    def test_conflict_requires_user_decision_and_unknown_remains_unverified(self):
        from customer_workflow.model_identity import verify
        from customer_workflow.recovery import DecisionRequired
        self.review('conflict')
        with self.assertRaises(DecisionRequired) as error:
            verify(self.task, self.observations)
        self.assertEqual(error.exception.decision['owner'], 'user')
        self.review('unknown')
        with self.assertRaisesRegex(ValueError, '身份'):
            verify(self.task, self.observations)

    def test_missing_or_changed_evidence_cannot_authorize_reuse(self):
        from customer_workflow.model_identity import verify
        self.review(evidence=[])
        with self.assertRaises(ValueError):
            verify(self.task, self.observations)
        self.review()
        (self.root / 'provenance.json').write_text('changed evidence')
        with self.assertRaises(ValueError):
            verify(self.task, self.observations)

    def test_changed_local_weights_cannot_reuse_old_identity_review(self):
        from model_prepare.customer import resolve
        from customer_workflow.model_identity import verify
        self.review()
        (self.model / 'pytorch_model.bin').write_bytes(b'changed synthetic weights')
        self.observations['model_resolution'] = resolve(self.task['model'], remote_lookup=self.remote)
        with self.assertRaises(ValueError):
            verify(self.task, self.observations)

    def test_local_only_does_not_need_repository_proof(self):
        from customer_workflow.model_identity import verify
        self.task['model']['model_repository_id'] = -1
        self.assertEqual(verify(self.task, self.observations)['conclusion'], 'not_requested')
        self.remote.assert_not_called()

    def test_reviewed_match_reuses_local_request_with_frozen_content(self):
        from customer_workflow.runner import prepare
        from customer_workflow.contract import normalize
        from customer_workflow.state import Session
        from coordination.runtime.sources import read
        self.review()
        self.observations.update(image_request={'reference': 'local:test'}, target={}, version_review=[])
        self.task['environment']['image_name'] = 'local:test'
        normalized = normalize(self.task)
        source = self.write('task.json', self.task)
        session = Session(self.root / 'runs', source['path'], normalized, 'match')
        with patch('customer_workflow.runner.execute', return_value={'status': 'completed'}) as execute:
            prepare(session, normalized, self.observations, self.root / 'runs')
        request = read(execute.call_args.args[3]['tasks']['model']['request'])
        self.assertEqual(request['source'], 'local')
        self.assertEqual(request['expected_identity'], self.resolution['asset']['identity'])
        self.assertEqual(session.value['steps']['model_identity']['value']['conclusion'], 'match')

    def test_remote_resolution_cannot_change_repository_or_explicit_path(self):
        from customer_workflow.model_identity import verify
        self.observations['model_resolution'] = {'request': {
            'source': 'modelscope', 'path': str(self.model), 'repository': 'other/model'}}
        with self.assertRaises(ValueError):
            verify(self.task, self.observations)
        self.observations['model_resolution']['request'].update(repository='org/model', path='/other/weights')
        with self.assertRaises(ValueError):
            verify(self.task, self.observations)

    def test_conflict_stops_model_consumers_but_independent_data_continues(self):
        from customer_workflow import runner
        from customer_workflow.contract import normalize
        from customer_workflow.state import Session
        self.review('conflict')
        source = self.write('task.json', self.task)
        session = Session(self.root / 'runs', source['path'], normalize(self.task), 'identity')
        before = {p.name: p.read_bytes() for p in self.model.iterdir()}
        with patch.object(runner, 'discover'), patch.object(runner, 'data_jobs', return_value=[]) as data, \
                patch.object(runner, 'migrate') as migrate, patch.object(runner, 'precision') as precision:
            code, result = runner.workflow(session, normalize(self.task), {'observations': self.observations}, self.root / 'runs',
                                           runner.context.installation({}, normalize(self.task)))
        self.assertEqual(code, 3)
        self.assertEqual(result['next_action']['owner'], 'user')
        data.assert_called_once()
        migrate.assert_not_called()
        precision.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.model.iterdir()})

    def test_later_agent_gap_does_not_keep_obsolete_user_conflict(self):
        from customer_workflow.runner import guarded
        from customer_workflow.recovery import DecisionRequired, pending
        from customer_workflow.contract import normalize
        from customer_workflow.state import Session
        source = self.write('task.json', self.task)
        session = Session(self.root / 'runs', source['path'], normalize(self.task), 'decisions')
        guarded(session, 'preparation', Mock(side_effect=DecisionRequired('旧模型冲突', {'kind': 'model_identity_conflict'})))
        self.assertEqual(pending(session)['owner'], 'user')
        guarded(session, 'preparation', Mock(side_effect=ValueError('新的观察缺口')))
        self.assertIsNone(pending(session))
        self.assertEqual(session.value['steps']['preparation']['diagnostic']['message'], '新的观察缺口')


if __name__ == '__main__':
    unittest.main()
