"""复用与重跑都经过精度阶段，记录正式测量及只读核验的原始回执。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'skills/vllm-precision-align/scripts'))
from alignment.evidence import ref
from quality_stage.measurement import select


class MeasurementSelectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.original = self.artifact('original', {'status': 'completed'})
        self.native = {'protocol': {'dataset': 'gsm8k'}, 'scores': [{'metric': 'accuracy', 'subset': None}]}
        self.row = {'metric': 'accuracy', 'subset': None, 'value': 0}
        self.static = {'chat_template_sha256': 'sha-template', 'tokenizer_sha256': 'sha-tokens',
                       'server_generation': {'overrides': {}}}
        projection = self.artifact('projection', {'static_conditions': self.static, 'scores': [self.row]})
        self.receipt = self.artifact('analysis', {'evidence': {'quality_evidence': projection}})
        self.invoke = Mock(return_value=(self.receipt, projection))

    def artifact(self, name, value):
        path = self.root / (name + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        return ref(path)

    def test_reuse_revalidates_source_and_keeps_original_receipt(self):
        result = select(self.native, [self.original], self.invoke)
        self.assertEqual(result['source'], 'reused')
        self.assertEqual(result['receipt'], self.original)
        self.assertEqual(result['verification'], self.receipt)
        self.assertEqual(self.invoke.call_args.args[:2],
                         ('analyze', {'schema_version': '1.0', 'source': self.original, 'expected': self.native}))
        self.invoke.assert_called_once()

    def test_no_candidate_runs_once_and_only_once(self):
        result = select(self.native, [], self.invoke)
        self.assertEqual(result['source'], 'rerun')
        self.assertEqual(result['receipt'], self.receipt)
        self.assertEqual(self.invoke.call_args.args[:2], ('quality-run', self.native))
        self.invoke.assert_called_once()

    def test_reuse_only_budget_never_sends_formal_inference_on_a_miss(self):
        with self.assertRaisesRegex(ValueError, '没有有效候选'):
            select(self.native, [], self.invoke, require_reuse=True)
        self.invoke.assert_not_called()

    def test_incomplete_static_settings_or_missing_total_try_other_candidates(self):
        missing_template = self.artifact('partial-template', {'scores': [self.row]})
        missing_total = self.artifact('partial-score', {'static_conditions': self.static, 'scores': []})
        complete = self.invoke.return_value
        self.invoke.side_effect = [(self.receipt, missing_template), (self.receipt, missing_total), complete]
        candidates = [self.artifact(str(i), {}) for i in range(3)]
        result = select(self.native, candidates, self.invoke)
        self.assertEqual(result['receipt'], candidates[-1])
        self.assertTrue(all(call.args[0] == 'analyze' for call in self.invoke.call_args_list))

    def test_invalid_candidate_does_not_hide_later_valid_one(self):
        bad = self.artifact('corrupt', {})
        bad['sha256'] = 'f' * 64
        result = select(self.native, [bad, self.original], self.invoke)
        self.assertEqual(result['receipt'], self.original)
        self.assertEqual(len(result['candidate_checks']), 2)
        self.invoke.assert_called_once()


class DiscoveredSourceRecheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        selector = {'name':'acc','dimensions':{},'aggregation':'mean'}
        definition = {'selector':selector,'sample_kind':'binary','sample_metric':'acc','unit':'ratio','direction':'higher'}
        self.protocol = {'dataset':'demo','model_identity':'a'*64,'dataset_revision':'v1','evaluator':'evalscope',
                         'subsets':['main'],'split':'test','generation_config':{'max_tokens':3},
                         'prompt_config':{'request_format':'chat'}, 'scoring_config':{'judge':{'strategy':'rule'}},
                         'metrics':{'acc':definition}}
        self.static = {'chat_template_sha256':'b'*64,'tokenizer_sha256':'c'*64,
                       'server_generation':{'overrides':{'--generation-config':'auto'},'model_generation_files':{}}}
        cfg = self.save('cfg',{'model':'model','model_id':'model','eval_type':'openai_api','repeats':1,
                        'dataset_args':{'demo':{'eval_split':'test','subset_list':['main']}},
                        'generation_config':self.protocol['generation_config'],'judge':{'strategy':'rule'}})
        stats = {'requested': 1, 'succeeded': 1, 'errored': 0}
        score = {'identity':selector,'semantics':{'kind':'quality','direction':'higher_is_better','raw_unit':'ratio'},
                 'num':1,'score':0,'categories':[{'subsets':[{'name':'main','num':1,'score':0}]}]}
        report = self.save('report',{'schema_version':2,'dataset_name':'demo','model_name':'model','metrics':[score],
                           'execution_summary':{**stats,'incomplete':False,'subsets':{'main':stats}}})
        review = self.save('review',{'index':0,'sample_score':{'sample_id':0,'score':{'status':'success','value':{'acc':0}}}})
        origin = self.save('origin',{'hardware':'gpu','status':'completed','run_id':'demo','protocol':self.protocol,
                           'static_conditions':self.static,'sample_ids':{'main':['sample-1']},
                           'files':{item['path']:item['sha256'] for item in (cfg,report,review)}})
        index = self.save('index',{'format':'evalscope-gpu-history-v1','source':origin,'config':cfg,
                                  'report':report,'reviews':{'main':review}})
        self.request = {'native_request':{'expected_static_conditions':self.static,'protocol':self.protocol,
                        'scores':[{'metric':'acc','subset':None}]},'gpu_evidence':index,
                        'baseline':{'evaluator':'evalscope','dataset':'demo','scores':[{'metric':'acc','gpu_score':0}]}}

    def save(self, name, obj):
        path = self.root / (name+'.json')
        path.write_text(json.dumps(obj)+'\n')
        return ref(path)

    def stage(self, should_start):
        from quality_stage import runner
        projected = self.save('projection', {})
        with patch.object(runner.Children, 'native', return_value=(projected, projected)) as infer, \
                patch.object(runner.Children, 'compare', return_value=projected):
            status, summary = runner.run(self.request, self.root/'attempts','eval','quality')
        self.assertEqual(status, 0, summary)
        self.assertEqual(infer.call_count, int(should_start))
        if not should_start:
            outcome = json.loads(Path(summary['quality_report']['path']).read_text())
            self.assertFalse(outcome['quality_measurement_started'])
            self.assertEqual(outcome['formal_requests'], 0)
            self.assertEqual(outcome['conclusions']['comparability'], 'not_evaluated')
        return summary

    def test_verified_unchanged_automatic_gpu_archive_allows_measurement(self):
        self.stage(True)

    def test_changed_archive_itself_skips_measurement(self):
        path = self.root/'index.json'
        path.write_text(path.read_text()+' ')
        self.stage(False)

    def test_missing_or_changed_attachments_skip_before_inference(self):
        for name in ('cfg','origin','report','review'):
            with self.subTest(name=name):
                path = self.root/(name+'.json')
                original = path.read_bytes()
                path.unlink()
                self.stage(False)
                path.write_bytes(original+b' ')
                self.stage(False)
                path.write_bytes(original)

    def test_source_change_during_historical_analysis_blocks_fallback_inference(self):
        from quality_stage import runner
        self.request['candidates'] = [self.save('candidate', {})]
        calls = []
        def analyze(action, request, label):
            calls.append(action)
            self.assertEqual(action, 'analyze')
            (self.root/'report.json').unlink()
            raise ValueError('候选协议不匹配')
        with patch.object(runner.Children, 'native', side_effect=analyze):
            code, result = runner.run(self.request, self.root/'attempts', 'fallback', 'quality')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['formal_requests'], 0)
        self.assertEqual(calls, ['analyze'])

    def test_declaration_score_changed_after_qualification_skips_measurement(self):
        self.request['baseline']['scores'][0]['gpu_score'] = 1
        self.stage(False)

    def test_automatic_without_gpu_archive_skips_explicit_baseline_still_runs(self):
        self.request.pop('gpu_evidence')
        self.stage(False)
        self.request['native_request'].pop('expected_static_conditions')
        self.stage(True)
