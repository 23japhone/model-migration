"""通过客户 CLI 验收三阶段。设备采集使用本地夹具，分数与张量走实际比较器。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_customer_dispatch import CustomerEntryFixture, SKILL
from build_tensor_stage_fixture import bind, common_scope, get, put, token_bundle, tensor_bundle
from customer_workflow import quality
from customer_workflow.quality import stage
from customer_workflow.state import identity

sys.path.insert(0, str(SKILL / 'skills/vllm-precision-align/scripts'))
from quality_stage import runner as quality_stage

run_quality = stage.run_one


class CustomerVerdicts(CustomerEntryFixture):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        score = {'metric': 'accuracy', 'gpu_score': 0.8,
                 'sample_results': [{'subset': 'main', 'sample_id': 2, 'score': 1}]}
        baseline = {'dataset': 'custom_qa', 'evaluator': 'evalscope', 'scores': [score]}
        self.raw = {'model': {}, 'environment': {}, 'accuracy': {
            'datasets': [{'name': 'custom_qa', 'path': str(self.root/'custom_qa')}], 'gpu_baselines': [baseline],
            'token_alignment': {'raw_logits': {'atol': 0}, 'processed_scores': {'atol': 0}}}}
        self.protocol, self.deployment = self.fixture_model()
        measured = {'subset': None, 'metric': 'accuracy', 'value': 0.79, 'unit': 'ratio', 'direction': 'higher',
                    'aggregation': 'mean', 'sample_kind': 'binary', 'samples': [
                        {'subset': 'main', 'sample_id': 2, 'score': 0}]}
        static = {'chat_template_sha256': 'b'*64, 'tokenizer_sha256': 'c'*64, 'server_generation': {'overrides': {}}}
        self.projection = put(self.root/'npu-measurement.json', {'format': 'task-quality-native-v1', 'hardware': 'npu',
                              'protocol': self.protocol, 'scores': [measured], 'static_conditions': static})

    def fixture_model(self):
        root = self.root / 'unavailable-weights'
        root.mkdir()
        files = {'config.json': put(root/'config.json', {})['sha256'], 'model.safetensors': 'f'*64}
        model = {'root': str(root), 'weight_files': ['model.safetensors'], 'files': files}
        handoff = put(self.root/'handoff.json', {'model': model})
        deployment = put(self.root/'deployment.json', {'task_id': 'verify-agent', 'status': 'completed', 'success': True,
                         'inputs': {'model': model}, 'evidence': {'handoff': handoff['path']},
                         'detailed_report': {'handoff_sha256': handoff['sha256']}})
        metrics = {'accuracy': {'unit': 'ratio', 'direction': 'higher', 'selector': {'name': 'acc', 'aggregation': 'mean'},
                                'sample_metric': 'acc', 'sample_kind': 'binary'}}
        return {'dataset': 'custom_qa', 'model_identity': identity(files), 'metrics': metrics,
                'generation_config': {'temperature': 0}}, deployment

    def native_measurement(self, action, request, label):
        native = put(self.root/(label+'.json'), {
            'task_id': 'verify-agent-plus', 'operation': 'quality-analyze' if action == 'analyze' else action,
            'status': 'completed', 'inputs': request, 'conclusions': {'evaluation_validity': 'pass'},
            'detailed_report': {'hardware': 'npu'}, 'evidence': {'quality_evidence': self.projection}})
        return native, self.projection

    def quality_entry(self, session, key, skill, request, root, action):
        # 在同一进程进入质量阶段，以便仅替换原生设备测量。其比较器和后续阶段均执行公开 CLI。
        code, result = quality_stage.run(quality_stage.validate(request), root, 'customer', action)
        self.assertEqual(code, 0, result)
        receipt = bind(Path(result['manifest']))
        session.step(key, 'completed', receipt=receipt, request=session.artifact(key, request))
        return get(receipt)

    def measured(self, session, task, observed, supplied, root, candidate, index):
        native = {'measurement': {'deployment': self.deployment}, 'protocol': self.protocol,
                  'scores': [{'metric': 'accuracy', 'subset': None}]}
        with patch.object(quality, 'prepare', return_value={'native_request': native}), \
                patch.object(quality_stage.Children, 'native', side_effect=self.native_measurement), \
                patch.object(stage, 'execute', side_effect=self.quality_entry):
            return run_quality(session, task, {**observed, 'deployment_receipt': self.deployment},
                               supplied, root, candidate, index)

    def assert_tensor(self, receipt, result, target):
        row = receipt['tensor_alignment'][0]
        manifest = get(row['stage'])
        report = get(row['tensor_report'])
        for output in (row, manifest, report):
            self.assertEqual((output['comparison_result'], output['target_status']), (result, target))
        summary = receipt['alignment_summary']['operator_tensor']
        self.assertEqual((summary['result'], summary['target_status']), (result, target))
        self.assertNotIn('hardware_target_status', summary)
        self.assertTrue(all('hardware_target_status' not in case for case in summary['cases']))
        self.assertEqual(row, get(bind(Path(receipt['evidence']['manifest'])))['tensor_alignment'][0])

    def test_custom_task_from_declared_gpu_score_to_all_tensor_exits(self):
        first = self.invoke([])
        source = first['task_quality'][0]['report']
        score = get(source)['scores'][0]
        self.assertEqual((score['drop'], score['target']['max_drop'], score['precision_target']), (0.01, 0.008, 'fail'))
        self.assertEqual(score['target_source'], 'default_relative')
        refs = [token_bundle(self.root, source, side) for side in ('gpu', 'npu')]
        scope = get(common_scope(self.root))
        refs.append(put(self.root/'scope.json', {**scope, 'model_identity': self.protocol['model_identity']}))
        second = self.invoke(refs, previous=first)
        token = second['token_alignment'][0]['token_report']
        case = get(token)['cases'][0]
        self.assertEqual((case['sample_id'], case['first_anomalous_token'], case['result']), (2, 2, 'mismatch'))
        self.assertEqual(case['cpu_check']['status'], 'unavailable')
        self.assert_tensor(second, 'insufficient_evidence', 'not_evaluated')
        plan = second['tensor_alignment'][0]['plans'][0]
        gpu = tensor_bundle(self.root/'gpu', plan, 'gpu', 0)
        for folder, delta, result, target in [('npu-aligned', 0, 'aligned', 'pass'),
                                               ('npu-outlier', 1, 'mismatch', 'fail')]:
            with self.subTest(result=result):
                npu = tensor_bundle(self.root/folder, plan, 'npu', delta)
                receipt = self.invoke([gpu, npu], previous=second, tensor_only=True)
                self.assertEqual(receipt['token_alignment'][0]['token_report'], token)
                self.assert_tensor(receipt, result, target)
