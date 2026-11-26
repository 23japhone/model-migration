"""通过客户回执验证任务执行状态与任务质量目标可以独立成立。"""
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coordination import cli
from coordination.runtime import sources
from customer_workflow import runner
from customer_workflow.quality import stage
from customer_workflow.state import Session
from test_customer_contract import customer


class CustomerConclusionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        task = {'accuracy': {'datasets': []}, 'model': {'path': -1}}
        path = self.root / 'task.json'
        path.write_text(json.dumps(task))
        self.session = Session(self.root / 'runs', path, task, 'customer')
        self.session.value['task_quality'] = []

    def score(self, subset, verdict, target=True):
        item = {'metric': 'acc', 'subset': subset, 'precision_target': verdict, 'comparability': 'pass',
                'gpu_score': .8, 'npu_score': .78, 'npu_minus_gpu': -.02, 'drop': .02, 'unit': 'ratio'}
        if target:
            item['target'] = {'max_drop': .01 if verdict == 'fail' else .03}
        return item

    def report(self, dataset, target, scores):
        source = self.session.artifact(dataset, {'task_id': 'vllm-precision-compare', 'operation': 'run',
            'status': 'completed', 'baseline': {'dataset': dataset}, 'scores': scores,
            'conclusions': {'precision_target': target, 'evaluation_validity': 'pass', 'comparability': 'pass'}})
        self.session.value['task_quality'].append({'dataset': dataset, 'status': 'completed', 'report': source})
        return source

    def result(self, status='completed'):
        code, value = self.session.finish(status)
        self.assertEqual(code, {'completed': 0, 'blocked': 3, 'failed': 1}[status])
        self.assertEqual(json.loads(self.session.path.read_text()), value)
        return value['task_quality_conclusion']

    def test_failure_survives_missing_peer_and_blocked_delivery(self):
        source = self.report('gsm8k', 'fail', [self.score(None, 'fail')])
        self.session.value['task_quality'].append({'dataset': 'ceval', 'status': 'blocked'})
        result = self.result('blocked')
        self.assertEqual(result['precision_target'], 'fail')
        self.assertIn('任务质量未达标', result['summary'])
        self.assertIn('阻塞', result['summary'])
        self.assertIn('ceval', result['summary'])
        self.assertEqual(result['reports'][0]['report'], source)
        score = result['reports'][0]['scores'][0]
        self.assertEqual((score['drop'], score['target']['max_drop'], score['unit']), (.02, .01, 'ratio'))

    def test_pass_is_limited_to_targets_across_reports(self):
        self.report('ceval', 'pass', [self.score(None, 'not_evaluated', False), self.score('math', 'pass')])
        self.report('gsm8k', 'not_evaluated', [self.score(None, 'not_evaluated', False)])
        result = self.result()
        self.assertEqual(result['precision_target'], 'pass')
        self.assertIn('math', result['summary'])
        self.assertIn('未设', result['summary'])
        self.assertIn('总分', result['summary'])

    def test_all_targets_pass(self):
        self.report('ceval', 'pass', [self.score('math', 'pass')])
        self.report('gsm8k', 'pass', [self.score(None, 'pass')])
        result = self.result()
        self.assertEqual(result['precision_target'], 'pass')
        self.assertTrue(result['summary'].startswith('任务已完成，本次约定的任务质量目标均已达标。'))

    def test_missing_or_changed_report_prevents_pass(self):
        self.report('gsm8k', 'pass', [self.score(None, 'pass')])
        ref = self.report('ceval', 'pass', [self.score(None, 'pass')])
        Path(ref['path']).write_text('{}')
        result = self.result()
        self.assertEqual(result['precision_target'], 'not_evaluated')
        self.assertIn('SHA256', result['summary'])

    def test_partial_legacy_list_cannot_pass_all_declared_baselines(self):
        task = dict(self.session.value['inputs'])
        task['accuracy'] = {'datasets': [], 'gpu_baselines': [{'dataset': 'ceval'}, {'dataset': 'gsm8k'}]}
        self.session = Session(self.root / 'runs', self.root / 'task.json', self.session.value['inputs'],
                               'customer', effective_task=task, quality_requested=True)
        self.session.value['task_quality'] = []
        self.report('ceval', 'pass', [self.score('math', 'pass')])
        result = self.result()
        self.assertEqual(result['precision_target'], 'not_evaluated')
        self.assertIn('gsm8k', result['summary'])
        self.assertIn('math acc 已达标', result['summary'])

    def test_no_threshold_displays_signed_difference(self):
        source = self.report('gsm8k', 'not_evaluated', [self.score(None, 'not_evaluated', False)])
        result = self.result()
        self.assertEqual(result['precision_target'], 'not_evaluated')
        self.assertIn('尚无达标判据', result['summary'])
        self.assertEqual(result['reports'][0]['scores'][0]['npu_minus_gpu'], -.02)
        invalid = sources.bound(source)
        invalid['conclusions']['evaluation_validity'] = 'not_evaluated'
        self.session.value['task_quality'][0]['report'] = self.session.artifact('invalid-measurement', invalid)
        self.assertIn('缺少有效测量', self.result()['summary'])

    def test_unavailable_gpu_keeps_measured_npu_score(self):
        score = {'metric': 'acc', 'comparability': 'review_required', 'precision_target': 'not_evaluated',
                 'npu_score': .78, 'unit': 'ratio', 'reasons': ['缺少 GPU 原生评分证据']}
        self.report('gsm8k', 'not_evaluated', [score])
        result = self.result()
        self.assertIn('缺少 GPU 原生评分证据', result['summary'])
        self.assertIn('NPU 得分 78%', result['summary'])

    def test_malformed_report_does_not_hide_failed_peer(self):
        self.report('gsm8k', 'fail', [self.score(None, 'fail')])
        self.report('ceval', 'not_evaluated', [{'metric': 'acc', 'precision_target': 'not_evaluated',
                                              'comparability': 'review_required', 'reasons': [None]}])
        result = self.result()
        self.assertEqual([r['precision_target'] for r in result['reports']], ['fail', 'not_evaluated'])
        self.assertIn('ceval', result['summary'])
        self.assertTrue(result['reports'][1]['missing'])

    def test_inconsistent_native_pass_cannot_accept_an_unverified_target(self):
        self.report('ceval', 'pass', [self.score('math', 'pass'), self.score('physics', 'not_evaluated')])
        result = self.result()
        self.assertEqual(result['precision_target'], 'not_evaluated')
        self.assertIn('约定目标', result['summary'])

    def test_cancellation_keeps_completed_report_and_unvisited_baseline(self):
        ref = self.report('gsm8k', 'fail', [self.score(None, 'fail')])
        task = {'accuracy': {'gpu_baselines': [{'dataset': 'gsm8k'}, {'dataset': 'ceval'}]}}

        def run_once(session, key, _operation):
            if key == 'quality-1':
                raise KeyboardInterrupt
            stage_ref = session.artifact('stage', {'quality_report': ref})
            session.step(key, 'completed', receipt=stage_ref)
            return {'quality_report': ref}

        with self.assertRaises(KeyboardInterrupt):
            stage.compare_all(self.session, task, {}, {}, self.root, run_once)
        result = self.result('failed')
        self.assertEqual(result['precision_target'], 'fail')
        self.assertIn('任务执行失败，任务质量未达标', result['summary'])
        self.assertIn('ceval', result['summary'])

    def test_unknown_intent_is_not_a_performance_only_task(self):
        result = self.result('blocked')
        self.assertEqual(result['precision_target'], 'not_evaluated')
        self.assertIn('意图', result['summary'])

    def invoke(self, task, accuracy):
        source = self.root / 'customer.json'
        source.write_text(json.dumps(task))
        context = self.root / 'context.json'
        context.write_text(json.dumps({'schema_version': '1.0', 'task_source': sources.ref(source),
            'intent': {'deployment': True, 'accuracy': accuracy, 'performance': True, 'conflicts': []},
            'observations': {}, 'evidence': []}))
        output = StringIO()
        with redirect_stdout(output):
            code = cli.main(['--task', str(source), '--context', str(context), '--work-root',
                             str(self.root / 'work'), '--run-id', 'customer'])
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt, sources.read(receipt['evidence']['manifest']))
        return code, receipt

    def deliver(self, task, quality_stage=None):
        from test_container_registration_continuation import ContainerRegistrationContinuationTests
        ordinary = ContainerRegistrationContinuationTests()
        ordinary.setUp()
        self.addCleanup(ordinary.doCleanups)
        entry, _ = ordinary.make_entry()
        def use_upstream(session, *_):
            for key in ('environment', 'preparation'):
                session.step(key, 'completed', value={'status': 'completed'})
            session.step('migration', 'completed', receipt=sources.ref(ordinary.migration_path))
            session.step('entry', 'completed', receipt=entry)
            session.value['resolved_deployment'] = {'capacity': {'max_model_len': 9000, 'max_num_seqs': 4,
                'kv_token_capacity': 9000, 'runtime_metrics_available': False}}

        with patch.object(runner, 'discover', side_effect=use_upstream), patch.object(runner, 'prepare', return_value=None), \
             patch.object(runner, 'data_jobs', return_value=[{'receipt': entry}]), \
             patch.object(runner.effects, 'operation', return_value=None), \
             patch.object(stage, 'run_one', side_effect=quality_stage) as quality:
            code, receipt = self.invoke(task, False)
        self.assertEqual(quality.call_count, int(quality_stage is not None))
        self.assertEqual((code, receipt['status'], receipt['success']), (0, 'completed', True))
        summary = receipt['alignment_summary']
        self.assertEqual(summary['task_quality'], receipt['task_quality_conclusion'])
        self.assertEqual(summary['operator_tensor']['result'], 'not_requested')
        self.assertIn('Token', summary['summary'])
        self.assertEqual(set(receipt['deliverables']), {'accuracy_plan', 'benchmark_plan', 'deployment'})
        for cfg in receipt['deliverables'].values():
            self.assertTrue(sources.bound(cfg))
        return receipt['task_quality_conclusion']

    def test_confirmed_performance_only(self):
        conclusion = self.deliver(customer())
        self.assertEqual(conclusion['precision_target'], 'not_requested')
        self.assertEqual(conclusion['summary'], '任务已完成，本次未要求任务质量对齐。')

    def test_public_customer_cli_delivers_three_files_with_failed_quality(self):
        score = dict(self.score(None, 'fail'), drop=.8 - .78, npu_minus_gpu=.78 - .8)
        source = self.report('ceval', 'fail', [score])
        task = customer()
        task['accuracy']['gpu_baselines'] = [{'dataset': 'ceval', 'evaluator': 'evalscope',
            'scores': [{'metric': 'acc', 'gpu_score': .8, 'target': {'max_drop': .01}}]}]

        def quality_stage(session, *_):
            value = {'operation': 'quality', 'quality_report': source}
            session.step('quality-0', 'completed', receipt=session.artifact('stage', value))
            return value

        conclusion = self.deliver(task, quality_stage)
        self.assertEqual(conclusion['precision_target'], 'fail')
        self.assertTrue(conclusion['summary'].startswith('任务已完成，任务质量未达标。'))
        self.assertIn('ceval', conclusion['summary'])
        self.assertIn('下降 2 个百分点，允许下降 1 个百分点', conclusion['summary'])
