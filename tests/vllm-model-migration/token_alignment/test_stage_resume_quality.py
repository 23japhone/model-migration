"""客户续接必须由当前候选复核锚定同一份首轮报告。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/scripts'


class FrozenQuality(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        from coordination.runtime import sources
        from customer_workflow.quality import resume
        self.sources = sources
        self.resume = resume
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.native = {'protocol': {'model_identity': 'same', 'dataset': 'test'}, 'measurement': {'service': 'same'}}
        self.gpu = self.put('gpu', {'original': 'native GPU'})
        self.npu = self.put('npu', {'original': 'native NPU'})
        self.scores = [{'metric': 'acc', 'precision_target': 'fail', 'comparability': 'pass'}]
        self.first, self.original = self.stage('original', 'rerun', self.npu)
        self.last, self.current = self.stage('current', 'reused', self.npu)
        self.session = SimpleNamespace(previous={'run_id': 'run', 'task_source': {'sha256': 'same'},
            'task_quality': [self.original], 'steps': {'quality-0': {'receipt': self.original['stage']}}},
            value={'run_id': 'run', 'task_source': {'sha256': 'same'}})

    def put(self, filename, data):
        file = self.root / (filename + '.json')
        file.write_text(json.dumps(data))
        return self.sources.ref(file)

    def stage(self, label, source, receipt):
        req = {'baseline': {'dataset': 'test'}, 'native_request': self.native, 'gpu_evidence': self.gpu}
        verification = receipt if source == 'rerun' else self.put(label+'-verification', {
            'task_id': 'verify-agent-plus', 'operation': 'quality-analyze', 'status': 'completed',
            'conclusions': {'evaluation_validity': 'pass'}, 'inputs': {'source': receipt, 'expected': self.native}})
        report = {'task_id': 'vllm-precision-compare', 'operation': 'run', 'status': 'completed',
                  'baseline': req['baseline'], 'scores': self.scores,
                  'conclusions': {'evaluation_validity': 'pass', 'precision_target': 'fail'},
                  'evidence': {'receipt': receipt, 'gpu_evidence': self.gpu, 'verification': verification},
                  'source': source}
        report_ref = self.put(label+'-report', report)
        stage = {'task_id': 'vllm-precision-align', 'operation': 'quality', 'status': 'completed',
                 'run_id': 'run', 'inputs': req, 'quality_report': report_ref,
                 'receipt': receipt, 'source': source, 'verification': verification}
        return stage, {'dataset': 'test', 'step': 'quality-0', 'status': 'completed',
                       'stage': self.put(label+'-stage', stage), 'report': report_ref}

    def test_preserve_report_digest_after_same_candidate_reverified(self):
        value = self.resume.frozen(self.session, self.current)
        self.assertEqual(value['report'], self.original['report'])
        self.assertEqual(value['stage'], self.original['stage'])
        self.assertEqual(value['verification_stage'], self.current['stage'])
        self.session.previous['task_quality'] = [value]
        self.session.previous['steps']['quality-0']['receipt'] = self.current['stage']
        self.assertEqual(self.resume.frozen(self.session, self.current)['report'], self.original['report'])

    def test_changed_protocol_or_different_selected_candidate_cannot_reuse(self):
        self.native = {'protocol': {'model_identity': 'new', 'dataset': 'test'}}
        _, current = self.stage('changed', 'reused', self.npu)
        self.assertEqual(self.resume.frozen(self.session, current), current)
        self.native = self.first['inputs']['native_request']
        _, current = self.stage('new-candidate', 'reused', self.put('new-npu', {'v': 2}))
        self.assertEqual(self.resume.frozen(self.session, current), current)
        self.session.value['task_source'] = {'sha256': 'changed'}
        self.assertEqual(self.resume.frozen(self.session, self.current), self.current)

    def test_changed_verification_or_old_score_cannot_be_used(self):
        Path(self.last['verification']['path']).write_text('{}')
        self.assertEqual(self.resume.frozen(self.session, self.current), self.current)
        self.scores = [{'metric': 'acc', 'precision_target': 'pass', 'comparability': 'pass'}]
        _, current = self.stage('changed-score', 'reused', self.npu)
        self.assertEqual(self.resume.frozen(self.session, current), current)

    def test_prefer_previous_measurement_after_explicit_candidates(self):
        candidate = self.resume.prior_candidate(self.session, 'quality-0', 'test',
                                                self.first['inputs']['baseline'], self.native, self.gpu)
        self.assertEqual(candidate, self.npu)
        drift = {**self.native, 'protocol': {'dataset':'test', 'model_identity':'new'}}
        self.assertIsNone(self.resume.prior_candidate(self.session, 'quality-0', 'test',
                                                      self.first['inputs']['baseline'], drift, self.gpu))
