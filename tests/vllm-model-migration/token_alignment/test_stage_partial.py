"""子任务部分成功时按完整冻结清单核对成功项和失败项，防止漏题冒充通过。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class NativePartial(unittest.TestCase):
    def setUp(self):
        scripts = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/scripts'
        sys.path.insert(0, str(scripts))
        self.addCleanup(sys.path.remove, str(scripts))
        from alignment import evidence
        from token_stage import measurement
        self.evidence = evidence
        self.native = measurement
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.keys = [{'subset':'math', 'sample_id': i} for i in (1, 2)]
        self.quality = {'path':'/fixed/quality.json', 'sha256':'e' * 64}
        self.selection = self.store(self.root / 'selection.json', {'cases': self.keys})
        self.req = {'schema_version':'1.0', 'native_request':{}, 'selection': self.selection,
                    'quality_report': self.quality}
        self.returned_errors = [{**self.keys[1], 'message': 'device error'}]

    def store(self, path, doc):
        self.evidence.write(path, doc)
        return self.evidence.ref(path)

    def child(self, argv, timeout, destination, label):
        folder = self.root / 'measurements/fixed'
        folder.mkdir(parents=True, exist_ok=True)
        bundle = self.store(folder/'bundle.json', {'format':'token-prediction-bundle-v1', 'hardware':'npu',
            'evidence_kind':'runtime','quality_report_sha256': self.quality['sha256'],
            'cases': [{**self.keys[0], 'record': {'path':'/unused', 'sha256':'a'*64}}]})
        path = folder / 'manifest.json'
        self.store(path, {'task_id':'verify-agent-plus','operation':'token-run','status':'failed','success':False,
                   'inputs':self.req, 'run_id':'run', 'evidence':{'token_bundle':bundle},
                   'detailed_report':{'case_errors': self.returned_errors}})
        return 1, json.dumps({'report_path':str(path)})

    def test_verified_partial_keeps_success(self):
        with patch.object(self.native, 'call', side_effect=self.child):
            result = self.native.run_measurement(self.req, self.root, 'run')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['case_errors'], self.returned_errors)

    def test_same_case_claimed_as_both_success_and_failure(self):
        self.returned_errors = [{**self.keys[0], 'message': 'duplicate!'}]
        with patch.object(self.native, 'call', side_effect=self.child), self.assertRaises(ValueError):
            self.native.run_measurement(self.req, self.root, 'run')

    def test_broken_partial_error_row_is_rejected_as_invalid_evidence(self):
        self.returned_errors = [None]
        with patch.object(self.native, 'call', side_effect=self.child), self.assertRaises(ValueError):
            self.native.run_measurement(self.req, self.root, 'run')

    def test_only_missing_device_cases_are_sent(self):
        indexed = {('gpu', ('math', i)): {'record': {}} for i in (1,2)}
        indexed['npu', ('math', 1)] = {'record': {}}
        source = {'native_request': {'protocol': {'dataset':'exam'}}, 'quality_report': self.quality}
        with patch.object(self.native, 'run_measurement', return_value={'status':'completed'}) as call:
            result = self.native.collect_npu(source, {'cases':self.keys}, self.selection, indexed, self.root,
                                             'run', source['native_request']['protocol'])
        self.assertEqual(result['status'], 'completed')
        selected = self.evidence.bound(call.call_args.args[0]['selection'])
        self.assertEqual(selected['cases'], [self.keys[1]])

    def test_cpu_can_use_semantically_reviewed_deployment(self):
        from token_stage.cpu_model import plain_greedy, verified_deployment
        original = {'task_id':'verify-agent','status':'completed','success':False,'conclusions':{'semantic':'review_required'},
                    'evidence':{'handoff':'/bound/frozen'}}
        raw = self.store(self.root / 'first.json', original)
        review = {'task_id':'verify-agent','operation':'review','status':'completed','success':True,
                  'inputs':{'source':raw},'conclusions':{'semantic':'pass'},'evidence':original['evidence']}
        self.assertEqual(verified_deployment(self.store(self.root/'review.json', review)), original)
        review.update(success=False)
        with self.assertRaises(ValueError):
            verified_deployment(self.store(self.root/'review.json', review))
        cfg = {'temperature':0, 'stream':False, 'retries':1, 'n':1, 'stop_seqs':['...'],
               'extra_body':{'chat_template_kwargs':{'enable_thinking':False}}}
        projection = {'protocol':{'generation_config':cfg},
                      'static_conditions':{'server_generation':{'overrides':{'--generation-config':'vllm'}}}}
        self.assertTrue(plain_greedy(projection))
        cfg['extra_body']['bad_words'] = ['bad']
        self.assertFalse(plain_greedy(projection))

    def test_greedy_accepts_dtype_and_server_seed_from_frozen_receipt(self):
        from token_stage.cpu_model import plain_greedy
        overrides = {'--dtype':'bfloat16', '--generation-config':'vllm', '--seed':'0'}
        projection = {'protocol':{'generation_config':{'temperature':0}},
                      'static_conditions':{'server_generation':{'overrides':overrides}}}
        self.assertTrue(plain_greedy(projection))
        overrides['--override-generation-config'] = '{"top_k":4}'
        self.assertFalse(plain_greedy(projection))
