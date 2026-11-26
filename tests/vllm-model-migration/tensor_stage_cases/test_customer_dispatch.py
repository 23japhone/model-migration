"""从客户 CLI 调度，使用固定的质量测量和合成双端预测，实际执行二、三遍 CLI。"""
import contextlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SKILL / 'scripts'))
from build_tensor_stage_fixture import common_scope, get, put, quality_fixture, token_bundle
from coordination.cli import main
from customer_workflow import runner
from customer_workflow.quality import stage


class CustomerEntryFixture(unittest.TestCase):
    def invoke(self, evidence, previous=None, prompt=None, published=False, tensor_only=False):
        source = put(self.root / 'task.json', self.raw)
        supplied = put(self.root / 'context.json', {
            'schema_version': '1.0', 'task_source': source, 'observations': {}, 'evidence': evidence,
            'prompt_fields': prompt or {},
            'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []}})
        argv = ['--task', source['path'], '--context', supplied['path'],
                '--work-root', str(self.root / 'work'), '--run-id', 'customer']
        if previous:
            argv.extend(('--resume', previous['evidence']['manifest']))
        output = io.StringIO()
        with patch.object(runner, 'discover'), patch.object(runner, 'prepare', return_value=None), \
                patch.object(runner, 'data_jobs', return_value=[]), \
                patch.object(stage, 'run_one', side_effect=self.measured) as quality, \
                contextlib.redirect_stdout(output):
            code = main(argv)
        receipt = json.loads(output.getvalue())
        if published:
            self.assertEqual(code, 0, receipt)
        else:
            self.assertEqual(code, 3, receipt)  # 本用例没有提供部署交付物。
        if tensor_only or published:
            quality.assert_not_called()
        else:
            quality.assert_called_once()
        self.assertEqual(receipt['task_quality_conclusion']['precision_target'], 'fail', receipt['task_quality_conclusion'])
        return receipt


class CustomerDispatch(CustomerEntryFixture):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.raw = json.loads((SKILL / 'assets/three-level-alignment.example.json').read_text())
        self.baseline = self.raw['accuracy']['gpu_baselines'][0]
        self.quality = quality_fixture(self.root, self.baseline)
        self.gpu, self.npu = [token_bundle(self.root, self.quality, side) for side in ('gpu', 'npu')]

    def measured(self, session, task, observed, supplied, root, candidate, index):
        self.assertEqual(candidate['baseline'], self.baseline)
        session.step(f'quality-{index}', 'completed')
        return {'quality_report': self.quality}

    def test_one_sided_token_evidence_survives_resume_without_tensor(self):
        del self.raw['accuracy']['tensor_alignment']
        first = self.invoke([self.npu])
        first_report = get(first['token_alignment'][0]['token_report'])
        self.assertEqual(first_report['cases'][0]['result'], 'insufficient_evidence')
        second = self.invoke([self.gpu], previous=first)
        token = get(second['token_alignment'][0]['stage'])
        self.assertCountEqual(token['inputs']['bundles'], [self.gpu, self.npu])
        self.assertEqual(token['inputs']['quality_report'], self.quality)
        case = get(token['token_report'])['cases'][0]
        self.assertEqual((case['result'], case['first_anomalous_token']), ('mismatch', 2))
        self.assertFalse(first.get('tensor_alignment'))
        tensor = get(second['tensor_alignment'][0]['stage'])
        self.assertEqual(tensor['inputs']['tensor_alignment'], {})
        self.assertEqual(tensor['inputs']['token_report'], token['token_report'])

    def test_tensor_empty_policy_dispatches_using_the_same_token_and_quality_reports(self):
        receipt = self.invoke([self.gpu, self.npu, common_scope(self.root)])
        token = get(receipt['token_alignment'][0]['stage'])
        tensor = get(receipt['tensor_alignment'][0]['stage'])
        self.assertEqual(token['inputs']['token_alignment'], self.raw['accuracy']['token_alignment'])
        self.assertEqual(tensor['inputs']['tensor_alignment'], {})
        self.assertEqual(token['inputs']['quality_report'], self.quality)
        self.assertEqual(tensor['inputs']['quality_report'], self.quality)
        self.assertEqual(tensor['inputs']['token_report'], token['token_report'])
        self.assertEqual(get(tensor['tensor_report'])['cases'][0]['result'], 'awaiting_gpu')

    def test_rerunning_tokens_creates_a_fresh_comparison_even_with_tensor_policy(self):
        first = self.invoke([self.gpu, self.npu])
        next_run = self.invoke([self.gpu, self.npu], previous=first)
        old, fresh = [get(item['token_alignment'][0]['token_report']) for item in (first, next_run)]
        self.assertNotEqual(old['cases'][0]['comparison'], fresh['cases'][0]['comparison'])

    def test_default_tensor_dispatch_only_submits_scopes_with_a_paired_first_difference(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from customer_workflow.tensor_alignment import stage as tensor_stage
        rows = []
        for outcome in ('early_stop', 'input_mismatch', 'insufficient_evidence', 'mismatch'):
            token = put(self.root/(outcome+'.json'), {'quality_report': self.quality, 'cases': [
                {'result': outcome, 'first_anomalous_token': 2}]})
            rows.append({'quality_report': self.quality, 'token_report': token})
        session = SimpleNamespace(value={'token_alignment': rows}, save=Mock())
        guarded = Mock(return_value={'status': 'completed'})
        tensor_stage.compare_all(session, None, {}, self.root, guarded)
        self.assertEqual([call.args[1] for call in guarded.call_args_list], ['tensor-3'])
        self.assertEqual(len(session.value['tensor_alignment']), len(rows))

    def test_current_prediction_bundles_can_replace_an_unreadable_old_comparison(self):
        del self.raw['accuracy']['tensor_alignment']
        first = self.invoke([self.npu])
        old_report = first['token_alignment'][0]['token_report']
        Path(old_report['path']).write_text('{}')
        second = self.invoke([self.gpu, self.npu], previous=first)
        token = get(second['token_alignment'][0]['token_report'])
        self.assertEqual(token['cases'][0]['first_anomalous_token'], 2)

    def test_damaged_unidentified_evidence_does_not_reuse_token_report(self):
        from customer_workflow.tensor_alignment.continuation import handoff_only
        source = put(self.root/'handoff.json', {'format': 'token-prediction-bundle-v1'})
        Path(source['path']).write_text('{}')
        self.assertFalse(handoff_only({}, {'evidence': [source]}))

    def test_tensor_enabled_by_prompt_accepts_damaged_handoff_as_a_local_diagnostic(self):
        del self.raw['accuracy']['tensor_alignment']
        prompt = {'accuracy.tensor_alignment': {}}
        old = self.invoke([self.gpu, self.npu, common_scope(self.root)], prompt=prompt)
        files = {name: put(self.root / (name + '.json'), {})
                 for name in ('benchmark_plan', 'accuracy_plan', 'deployment')}
        export = put(self.root / 'export.json', {'status': 'ready', 'deliverables': files})
        old.update(status='completed', deliverables=files, export_manifest=export['path'])
        old['steps']['delivery'] = {'status': 'completed', 'receipt': export}
        put(Path(old['evidence']['manifest']), old)
        damaged = put(self.root / 'gpu-handoff.json', {'format': 'tensor-bundle-v1'})
        put(Path(damaged['path']), {'format': 'tensor-bundle-v1', 'truncated': True})
        resumed = self.invoke([damaged], previous=old, prompt=prompt, published=True)
        self.assertEqual(resumed['deliverables'], files)
        self.assertEqual(resumed['token_alignment'], old['token_alignment'])
        issues = get(resumed['tensor_alignment'][0]['tensor_report'])['uncovered']
        self.assertIn(damaged, [issue['source'] for issue in issues])
