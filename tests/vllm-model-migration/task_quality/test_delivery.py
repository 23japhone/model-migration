"""客户质量失败、成绩复用及三文件交付在同一运行中各自有独立结论。"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from customer_workflow import runner
from customer_workflow.quality import stage


class CustomerQualityTests(unittest.TestCase):
    def test_unavailable_baseline_keeps_other_report_and_three_file_delivery(self):
        baseline = {'evaluator': 'evalscope', 'dataset': 'gsm8k', 'scores': [{'metric': 'accuracy', 'gpu_score': 1}]}
        task = {'accuracy': {'evaluator': 'evalscope', 'gpu_baselines': [baseline, baseline]}}
        session = Mock()
        session.value = {'steps': {'entry': {'receipt': 'base-entry'}}, 'resolved_deployment': {'capacity': {}}}
        session.ready.return_value = {'status': 'completed'}
        session.step.side_effect = lambda key, status, **details: session.value['steps'].update(
            {key: {'status': status, **details}})

        def one(*args):
            if args[-1] == 0:
                raise ValueError('原生配置缺失')
            session.step('quality-1', 'completed', receipt='quality-stage')
            return {'quality_report': 'second-score-report'}

        with patch.object(stage, 'run_one', side_effect=one):
            stage.compare_all(session, task, {}, {}, Path('/unused'), runner.guarded)
        first, second = session.value['task_quality']
        self.assertEqual(first['status'], 'blocked')
        self.assertEqual(second['report'], 'second-score-report')
        with patch.object(runner, 'pending', return_value=False), patch.object(runner, 'deliver') as deliver:
            deliver.return_value = {'deliverables': ['a','b','c'], 'manifest': 'export'}
            runner.complete(session, task, {}, {'runtime_environment': {'version': 'fixed'}},
                            Path('/unused'), [{'receipt': 'ordinary-measurement'}])
        session.finish.assert_called_once_with('completed', deliverables=['a','b','c'], export_manifest='export')

    def test_performance_only_intent_without_baselines_skips_quality_discovery(self):
        session = Mock()
        stage.compare_all(session, {'accuracy': {}}, {}, {'intent': {'accuracy': False}}, Path('/unused'), Mock())
        session.save.assert_not_called()

    def test_only_previous_measurements_of_same_dataset_are_discovered(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            directory = root / 'old/vllm-precision-align/quality/attempt'
            directory.mkdir(parents=True)
            source = {'path': '/original', 'sha256': 'a' * 64}
            data = {'task_id': 'vllm-precision-align', 'inputs': {'baseline': {'dataset':'ceval'}}, 'receipt': source}
            (directory / 'quality-stage-manifest.json').write_text(json.dumps(data))
            self.assertEqual(list(stage.recent_measurements(root, 'ceval')), [source])
            self.assertEqual(list(stage.recent_measurements(root, 'gsm8k')), [])


class AutomaticBaselineTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.ready.return_value = None
        self.session.parent = Path('/unused')
        self.session.value = {'steps': {}}
        self.session.artifact.side_effect = lambda key, value: {'path': '/' + key, 'sha256': 'a' * 64}
        self.session.step.side_effect = lambda key,status,**kw: self.session.value['steps'].update(
            {key: {'status': status, **kw}})
        self.task = {'model': {'model_name':'Qwen3.5-27B'}, 'accuracy': {}}
        self.intent = {'intent': {'accuracy': True}}

    def test_no_result_or_missing_evidence_is_local_to_quality(self):
        for missing in (['没有找到 Qwen3.5-27B 历史评测'], ['缺少数据集版本和权重身份']):
            with self.subTest(missing=missing), patch.object(stage.selection, 'discover') as search, patch.object(stage, 'run_one') as run:
                search.return_value = {'status':'not_evaluated','baselines':[],'sources':[],'missing':missing}
                stage.compare_all(self.session,self.task,{},self.intent,Path('/unused'),runner.guarded)
                run.assert_not_called()
                self.assertEqual(self.session.value['task_quality'][0]['status'], 'not_evaluated')
                self.assertEqual(self.session.value['task_quality'][0]['missing'], missing)
                self.session.finish.assert_not_called()

    def test_discovered_protocol_difference_allows_native_measurement(self):
        from customer_workflow import quality
        explicit = {'evaluator':'evalscope','dataset':'gsm8k','scores':[{'metric':'accuracy','gpu_score':1}]}
        p = {k: 'hash' for k in ('dataset', 'dataset_revision', 'dataset_identity', 'model_identity',
                                   'task_name', 'task_revision','evaluator','evaluator_version','subsets',
                                   'split','sample_identities','prompt_config','generation_config','scoring_config')}
        p['metrics']={'accuracy':{'unit':'ratio'}}
        entry={'baseline':explicit,'gpu_evidence':{'path':'/gpu','sha256':'a'*64},'protocol':p}
        self.session.ready.return_value={'tasks':{'verify':{'receipt':'deployment'}}}
        with patch.object(stage.selection, 'discover', return_value={'status':'found','baselines':[entry]}), \
             patch.object(quality, 'prepare', return_value={'protocol':{**p,'dataset_revision':'different'}, 'native_request':{'protocol': p}}), \
             patch.object(stage, 'execute') as execute:
            stage.compare_all(self.session, self.task, {}, self.intent, Path('/unused'), runner.guarded)
        execute.assert_called_once()
        request = execute.call_args.args[3]
        self.assertEqual('publisher', request['baseline_source'])
        self.assertNotIn('expected_static_conditions', request['native_request'])


class DiscoveryDeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        from customer_workflow.state import Session
        from coordination.runtime import sources
        from importlib import import_module

        tests = Path(__file__).resolve().parent
        for directory in (tests.parent, ROOT / 'skills/vllm-precision-align/scripts'):
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))
                self.addCleanup(sys.path.remove, str(directory))
        module = import_module((__package__ + '.' if __package__ else '') + 'test_baseline_qualification')
        self.published = module.PublicBaselineQualification()
        self.published.setUp()
        self.addCleanup(self.published.doCleanups)
        self.work = self.published.root / 'runs'
        self.task = {'model': {**self.published.task['model'], 'path': -1},
                     'environment': {'container_name': 'vllm_demo', 'available_npu_devices': [0]},
                     'accuracy': {'evaluator': 'evalscope', 'datasets': []}}
        task_file = self.published.root / 'customer-task.json'
        module.save(task_file, self.task)
        self.session = Session(self.work / 'customer' / 'customer-workflow', task_file, self.task, 'customer')
        migration = self.session.artifact('deployment-stage', {
            'tasks': {'verify': {'receipt': self.published.origin['deployment_receipt']}}})
        self.session.step('migration', 'completed', receipt=migration)
        self.sources = sources

    def published_urls(self, targets=()):
        import json
        history = self.published
        history.evidence(published=targets)
        prefix = history.origin['artifact_url'].split('gpu-eval')[0]
        members = ['gpu-eval/' + n for n in ('index.json', 'gpu.json', 'config.json', 'report.json')]
        names = ['README.md', *members]
        info = {'id': 'org/model', 'sha': history.origin['revision'], 'siblings': [{'rfilename': n} for n in names]}
        result = {'https://huggingface.co/api/models/org/model': json.dumps(info).encode(),
                  prefix + 'README.md': b'Official results'}
        for name in members:
            result[prefix + name] = (history.root / Path(name).name).read_bytes()
        return result

    def align_process(self, args, timeout, attempt, label):
        from alignment import cli
        from contextlib import redirect_stdout
        from io import StringIO
        self.assertEqual('precision.py', Path(args[2]).name)
        self.assertEqual('quality', args[args.index('--action') + 1])
        output = StringIO()
        with redirect_stdout(output):
            code = cli.main(args[3:])
        return code, output.getvalue()

    def native_measurement(self, action, native, label):
        self.assertEqual('quality-run', action)
        self.assertNotIn('expected_static_conditions', native)
        scores = [{**scope, 'value': 0.5, 'unit': 'ratio', 'direction': 'higher',
                   'aggregation': 'mean', 'sample_kind': 'binary', 'samples': []} for scope in native['scores']]
        projection = self.session.artifact('npu-data', {'format': 'task-quality-native-v1', 'hardware': 'npu',
            'protocol': self.published.proto, 'static_conditions': self.published.static, 'scores': scores})
        receipt = self.session.artifact('npu-source', {'task_id':'verify-agent-plus', 'operation':action,
            'status':'completed', 'conclusions':{'evaluation_validity':'pass'}, 'detailed_report':{'hardware':'npu'},
            'evidence': {'quality_evidence':projection}, 'inputs':native})
        return receipt, projection

    def reported_alignment(self, targets=()):
        from customer_workflow import invoke, quality
        from quality_stage.children import Children
        urls = self.published_urls(targets)
        scores = [{'subset': s, 'metric': 'acc'} for s in (None, 'a')]
        native = {'protocol': self.published.proto, 'scores': scores}
        with patch('customer_workflow.quality.baseline_search.transport.fetch', side_effect=lambda url, timeout: urls[url]), \
             patch.object(quality, 'prepare', return_value={'protocol': self.published.proto, 'native_request': native}) as prepared, \
             patch.object(invoke, 'call', side_effect=self.align_process) as dispatched, \
             patch.object(Children, 'native', side_effect=self.native_measurement) as measured:
            stage.compare_all(self.session, self.task, {}, {'intent': {'accuracy': True}}, self.work, runner.guarded)
        quality_stage = self.session.value['task_quality'][0]
        self.assertEqual('completed', quality_stage['status'], self.session.value['steps'])
        dispatched.assert_called_once()
        measured.assert_called_once()
        search = self.sources.bound(self.session.value['gpu_baseline_search'])
        report = self.sources.bound(quality_stage['report'])
        self.assertEqual('found', search['status'])
        expected = search['baselines'][0]['gpu_evidence']
        self.assertEqual(expected, prepared.call_args.kwargs['public_gpu'])
        self.assertEqual(expected, report['evidence']['gpu_evidence'])
        self.assertEqual('pass', report['conclusions']['comparability'])
        return report

    def test_found_complete_baseline_dispatches_public_quality_once_without_a_target(self):
        report = self.reported_alignment()
        self.assertEqual('pass', report['conclusions']['precision_target'])
        conclusion = self.session.finish('blocked')[1]['task_quality_conclusion']
        self.assertEqual('pass', conclusion['precision_target'])
        self.assertIn('均已达标', conclusion['summary'])
        for row in report['scores']:
            self.assertEqual(0, row['target']['max_drop'])
            self.assertEqual(0.5, row['npu_minus_gpu'])

    def test_public_gpu_target_reaches_comparison_in_customer_delivery(self):
        report = self.reported_alignment([{'metric':'acc', 'subset':'a', 'gpu_score':0, 'target':{'max_drop':0}}])
        self.assertEqual('pass', report['conclusions']['precision_target'])
        total, subset = report['scores']
        self.assertEqual('pass', total['precision_target'])
        self.assertEqual('pass', subset['precision_target'])
        self.assertEqual('publisher', subset['target_evidence']['kind'])
        conclusion = self.session.finish('blocked')[1]['task_quality_conclusion']
        self.assertEqual('pass', conclusion['precision_target'])
        self.assertIn('a acc 已达标', conclusion['summary'])
        self.assertIn('总分 acc 已达标', conclusion['summary'])
        original = self.sources.bound(subset['target_evidence']['source'])
        self.assertEqual({'max_drop':0}, original['scores'][0]['target'])

    def test_public_network_unavailable_keeps_real_three_file_publication(self):
        from test_container_registration_continuation import ContainerRegistrationContinuationTests
        ordinary = ContainerRegistrationContinuationTests()
        ordinary.setUp()
        self.addCleanup(ordinary.doCleanups)
        entry, _ = ordinary.make_entry()
        self.session.step('entry', 'completed', receipt=entry)
        for key in ('environment', 'preparation'):
            self.session.step(key, 'completed', value={'status':'completed'})
        self.session.step('migration', 'completed', receipt=self.sources.ref(ordinary.migration_path))
        self.session.value['resolved_deployment'] = {'capacity': {
            'max_model_len':9000, 'max_num_seqs':4, 'kv_token_capacity':9000, 'runtime_metrics_available':False}}
        intent = {'intent': {'accuracy': True}}
        with patch('customer_workflow.quality.baseline_search.transport.fetch', side_effect=OSError('offline')) as net, \
             patch.object(stage, 'execute') as inference, patch.object(runner.effects, 'operation', return_value=None):
            stage.compare_all(self.session, self.task, {}, intent, self.work, runner.guarded)
            code, customer = runner.complete(self.session, self.task, {}, intent, self.work, [{'receipt': entry}])
        net.assert_called_once()
        inference.assert_not_called()
        quality = customer['task_quality'][0]
        self.assertEqual('not_evaluated', quality['status'])
        self.assertFalse(quality['quality_measurement_started'])
        self.assertTrue(quality['missing'])
        conclusion = customer['task_quality_conclusion']
        self.assertEqual('not_evaluated', conclusion['precision_target'])
        self.assertIn(quality['missing'][0], conclusion['summary'])
        self.assertEqual(0, code)
        self.assertEqual('ready', self.sources.read(customer['export_manifest'])['status'])
        self.assertEqual({'benchmark_plan', 'accuracy_plan', 'deployment'}, set(customer['deliverables']))
        for name, ref in customer['deliverables'].items():
            self.assertEqual(name + '.json', Path(ref['path']).name)
            self.assertTrue(self.sources.bound(ref))
