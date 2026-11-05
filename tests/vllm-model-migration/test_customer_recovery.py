"""客户续接必须复用相同来源，并保留独立的成功项。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/vllm-model-migration/scripts'
sys.path.insert(0, str(SCRIPTS))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def document(self, name, value):
        from coordination.runtime.sources import ref
        path = self.root / name
        path.write_text(json.dumps(value))
        return ref(path)

    def test_resume_harvests_only_valid_completed_evaluations(self):
        from customer_workflow.recovery import evaluations
        good = self.document('good.json', {'status': 'completed', 'conclusions': {'evaluation_validity': 'pass'}})
        bad = self.document('bad.json', {'status': 'completed', 'conclusions': {'evaluation_validity': 'fail'}})
        stage = self.document('precision.json', {'tasks': {'evaluations': {
            'good': {'status': 'completed', 'receipt': good},
            'bad': {'status': 'completed', 'receipt': bad}, 'missing': {'status': 'blocked'}}}})
        entry = self.document('entry.json', {'stages': {'precision': {'receipt': stage}}})
        self.assertEqual(evaluations({'steps': {'entry': {'receipt': entry}}}), {'good': good})

    def test_context_evidence_accepts_text_but_checks_bytes(self):
        from customer_workflow.context import load
        from coordination.runtime.sources import ref
        source = self.document('task.json', {})
        proof = self.root / 'official.txt'
        proof.write_text('official compatibility evidence')
        context = self.document('context.json', {'schema_version': '1.0', 'task_source': source,
            'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
            'observations': {}, 'evidence': [ref(proof)]})
        self.assertIsNotNone(load(context['path'], source))
        proof.write_text('changed')
        with self.assertRaises(ValueError):
            load(context['path'], source)

    def test_container_discovery_requires_identity_bound_approval(self):
        from customer_workflow.recovery import container, DecisionRequired
        identity = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'started_at': 'now'}
        with self.assertRaises(DecisionRequired):
            container(None, {'container_name': 'vllm_model'}, {'container': identity}, {}, self.root)
        with patch('customer_workflow.recovery.operation') as operation:
            container(None, {'container_name': 'vllm_model'}, {'container': identity},
                      {'container_replacement': {'approved': True, 'identity': identity}}, self.root)
            self.assertEqual(operation.call_args.args[3]['identity'], identity)

    def test_exited_container_is_replaced_without_user_decision(self):
        from customer_workflow.recovery import container
        identity = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'started_at': 'old-start'}
        observed = {**identity, 'status': 'exited', 'running': False}
        with patch('customer_workflow.recovery.operation') as operation:
            container(None, {'container_name': 'model'}, {'container': observed}, {}, self.root)
        self.assertEqual(operation.call_args.args[3], {'operation': 'replace_container',
                         'container_name': 'model', 'identity': identity})

    def test_non_exited_or_unproven_state_keeps_identity_bound_consent(self):
        from customer_workflow.recovery import container, DecisionRequired
        identity = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'started_at': 'old-start'}
        for state in ({}, {'status': 'exited'}, {'status': 'exited', 'running': True},
                      {'status': 'created', 'running': False}, {'status': 'running', 'running': True}):
            with self.subTest(state=state), patch('customer_workflow.recovery.operation') as operation:
                with self.assertRaises(DecisionRequired):
                    container(None, {'container_name': 'model'}, {'container': {**identity, **state}}, {}, self.root)
                operation.assert_not_called()
                consent = {'approved': True, 'identity': identity}
                container(None, {'container_name': 'model'}, {'container': {**identity, **state}},
                          {'container_replacement': consent}, self.root)
                self.assertEqual(operation.call_args.args[3]['consent'], consent)

    def test_replacement_success_continues_deployment_and_failure_stops_it(self):
        from customer_workflow.runner import migrate
        from unittest.mock import Mock
        identity = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'started_at': 'old-start',
                    'status': 'exited', 'running': False}
        recipe = {'model_max_context': 9000, 'max_num_seqs': 1, 'runtime_metrics_available': False}
        task = {'environment': {'container_name': 'model'}}
        for fail in (False, True):
            with self.subTest(fail=fail):
                session = Mock(value={'steps': {'preparation': {'receipt': {'path': '/prepared.json'}},
                                              'allocation': {'receipt': {'path': '/allocation.json', 'sha256': 'b' * 64}},
                                              'host_observation': {'receipt': {'path': '/observed.json', 'sha256': 'a' * 64}}}})
                session.ready.side_effect = lambda name: {'ready': True} if name == 'preparation' else None
                session.artifact.return_value = {'path': '/compiled-request.json'}
                events = []
                def replace(*args):
                    events.append('replace')
                    if fail:
                        raise ValueError('删除失败')
                def execute(*args):
                    events.append('deploy')
                    return {'status': 'completed'}
                operations = {'allocation': {'kv_token_capacity': 10000},
                              'host_observation': {'port': 8000, 'container': identity}}
                with patch('customer_workflow.effects.precheck_plan', return_value={'path': '/precheck.json'}), \
                        patch('customer_workflow.effects.functional_inputs', return_value=({}, 9000)), \
                        patch.multiple('customer_workflow.runner.deployment', prepared_model=Mock(return_value={}),
                                    capacity_spec=Mock(return_value={}), allocation_request=Mock(return_value={}),
                                    precheck=Mock(return_value={}), verify=Mock(return_value={
                                        'npu': {}, 'service': {'endpoint': 'http://127.0.0.1:8000'}})), \
                        patch('customer_workflow.runner.effects.operation', side_effect=lambda *a: operations[a[1]]), \
                        patch('customer_workflow.recovery.operation', side_effect=replace), \
                        patch('customer_workflow.runner.execute', side_effect=execute):
                    if fail:
                        with self.assertRaisesRegex(ValueError, '删除失败'):
                            migrate(session, task, {'deployment': recipe}, [], self.root, {})
                    else:
                        self.assertEqual(migrate(session, task, {'deployment': recipe}, [], self.root, {})['status'],
                                         'completed')
                self.assertEqual(events, ['replace'] if fail else ['replace', 'deploy'])

    def test_aisbench_resume_preserves_task_environment_versions(self):
        from customer_workflow.effects import evaluator
        from unittest.mock import Mock
        session = Mock()
        session.ready.return_value = {'result': {'versions': {'ais-bench-benchmark': '1.2.3'}}}
        with patch('customer_workflow.effects.operation') as operation:
            evaluator(session, {'accuracy': {'evaluator': 'aisbench'}}, self.root)
            self.assertEqual(operation.call_args.args[3]['versions'], {'ais-bench-benchmark': '1.2.3'})

    def test_semantic_review_pending_does_not_start_a_second_service(self):
        from customer_workflow.runner import migrate
        from unittest.mock import Mock
        source = self.document('migration.json', {'status': 'blocked', 'next_action': 'semantic_review_required'})
        session = Mock()
        session.ready.return_value = None
        session.value = {'steps': {'migration': {'receipt': source}}}
        with patch('customer_workflow.runner.execute') as execute, patch('customer_workflow.effects.operation') as operation:
            result = migrate(session, {}, {}, [], self.root, {})
            self.assertEqual(result['status'], 'blocked')
            execute.assert_not_called()
            operation.assert_not_called()

    def test_new_data_must_fit_the_retained_service_without_truncation(self):
        from customer_workflow.runner import evaluation_request
        from unittest.mock import Mock
        session = Mock(value={'resolved_deployment': {'capacity': {'max_model_len': 9000}}})
        with self.assertRaisesRegex(ValueError, '8192'):
            evaluation_request(session, {}, {'subset': {'max_prompt_tokens': 1000}}, {}, 'http://127.0.0.1:8000')

    def test_prompt_default_resolution_preserves_explicit_json(self):
        from customer_workflow.intent import resolved
        self.assertEqual(resolved({'environment': {'image_name': -1}}, {'environment.image_name': 'local:test'}),
                         {'environment': {'image_name': 'local:test'}})
        with self.assertRaises(ValueError):
            resolved({'environment': {'image_name': 'local:old'}}, {'environment.image_name': 'local:new'})


if __name__ == '__main__':
    unittest.main()
