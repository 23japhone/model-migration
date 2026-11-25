"""验证质量报告到第二遍选题、两端采集与单题比较的公开阶段交接。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
STAGE = ROOT / 'skills/vllm-model-migration/skills/vllm-precision-align/scripts/precision.py'
TOL = {'raw_logits': {'atol': 0.01}, 'processed_scores': {'atol': 0.01}}


class TokenStage(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.rows = [self.score_row('math')]
        self.report = self.quality(self.rows)

    def put(self, name, value):
        path = self.directory / name
        data = json.dumps(value).encode()
        path.write_bytes(data)
        return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}

    def quality(self, rows, evidence=None):
        failed = [{'metric': r['metric'], 'subset': r['subset'], 'dataset': 'exam'} for r in rows
                  if r['precision_target'] == 'fail']
        report = {'task_id': 'vllm-precision-compare', 'operation': 'run', 'status': 'completed',
                  'baseline': {'dataset': 'exam', 'evaluator': 'evalscope'}, 'scores': rows,
                  'evidence': evidence or {}, 'conclusions': {'evaluation_validity': 'pass',
                  'precision_target': 'fail' if failed else 'pass', 'comparability': 'pass'}}
        if failed:
            report['next_action'] = {'requires_separate_run': True, 'failed_scores': failed}
        return self.put('quality.json', report)

    @staticmethod
    def score_row(subset):
        items = [{'subset': subset or 'math', 'sample_id': 2, 'gpu_score': 1, 'npu_score': 0,
                  'npu_minus_gpu': -1, 'change': 'correct_to_wrong'},
                 {'subset': subset or 'math', 'sample_id': 4, 'gpu_score': 0, 'npu_score': 1,
                  'npu_minus_gpu': 1, 'change': 'wrong_to_correct'}]
        return {'metric': 'accuracy', 'subset': subset, 'direction': 'higher', 'precision_target': 'fail',
                'comparability': 'pass', 'sample_comparison': {'status': 'completed', 'items': items,
                'expected': 2, 'matched': 2, 'unmatched': []}}

    def run_stage(self, bundles=(), plan=False, native=None):
        value = {'schema_version': '1.0', 'quality_report': self.report, 'token_alignment': TOL,
                 'bundles': list(bundles)}
        if native is not None:
            value['native_request'] = native
        request = self.put('request.json', value)
        cmd = [sys.executable, '-B', str(STAGE), '--action', 'token', '--request', request['path'],
               '--work-root', str(self.directory / 'work'), '--run-id', 'token-case']
        result = subprocess.run(cmd + (['--plan'] if plan else []), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        summary = json.loads(result.stdout)
        if plan:
            return summary
        return (summary, json.loads(Path(summary['selection']['path']).read_text()),
                json.loads(Path(summary['token_report']['path']).read_text()))

    def bundle(self, device, synthetic=False, quality_digest=None):
        kind = 'synthetic' if synthetic else 'runtime'
        record = self.put(device + '.json', {'sample_id': 2, 'subset': 'math', 'prompt_token_ids': [1],
                          'output_token_ids': [0], 'stop_reason': 'eos',
                          'raw_logits': [[2, 1]], 'processed_probs': [[0.75, 0.25]], 'evidence_kind': kind})
        review = self.put(device + '-review.json', {'sample_id': 2, 'subset': 'math', 'hardware': device,
                          'metrics': {'accuracy': int(device == 'gpu')}, 'quality_report_sha256': self.report['sha256'],
                          'evidence_kind': kind, 'record_sha256': record['sha256']})
        return self.put(device + '-bundle.json', {'format': 'token-prediction-bundle-v1', 'hardware': device,
                        'quality_report_sha256': quality_digest or self.report['sha256'], 'evidence_kind': kind,
                        'cases': [{'subset': 'math', 'sample_id': 2, 'record': record, 'review': review}]})

    def test_plan_does_not_read_sources(self):
        self.report = {'path': '/abs/missing.json', 'sha256': 'a' * 64}
        self.assertEqual(self.run_stage(plan=True)['status'], 'not_run')
        self.assertFalse((self.directory / 'work').exists())

    def test_selects_failed_rows_only_and_deduplicates_total(self):
        bad = self.score_row('ignored')
        bad['comparability'] = 'review_required'
        bad['precision_target'] = 'not_evaluated'
        self.report = self.quality([bad, self.score_row(None), self.score_row('math')])
        summary, selected, output = self.run_stage()
        self.assertEqual(summary['alignment_status'], 'insufficient_evidence')
        self.assertEqual([(s['subset'], s['sample_id']) for s in selected['cases']], [('math', 2)])
        self.assertEqual(len(selected['cases'][0]['triggers']), 2)
        self.assertEqual(output['cases'][0]['result'], 'insufficient_evidence')

    def test_scope_without_verified_roster_stays_pending_despite_bundle(self):
        self.rows[0].pop('sample_comparison')
        self.report = self.quality(self.rows)
        summary, selected, _ = self.run_stage([self.bundle('gpu')])
        self.assertEqual(summary['selected_count'], 0)
        self.assertEqual(selected['pending_scopes'][0]['subset'], 'math')

    def test_pair_runs_public_compare_and_keeps_synthetic_explicit(self):
        summary, _, output = self.run_stage([self.bundle('gpu', synthetic=True), self.bundle('npu')])
        case = output['cases'][0]
        self.assertEqual(case['result'], 'aligned')
        self.assertEqual(case['first_anomalous_token'], None)
        self.assertEqual(case['evidence_kind'], {'gpu': 'synthetic', 'npu': 'runtime'})
        self.assertEqual(case['quality_reproduction'][0]['status'], 'reproduced')
        self.assertEqual(summary['alignment_status'], 'simulated')

    def test_external_runtime_labels_only_describe_supplied_predictions(self):
        summary, _, output = self.run_stage([self.bundle('gpu'), self.bundle('npu')])
        self.assertEqual(summary['alignment_status'], 'aligned')
        self.assertEqual(output['cases'][0]['result'], 'aligned')
        self.assertEqual(output['comparison_scope'], 'provided_predictions')
        self.assertNotIn('real_hardware_alignment', output)

    def test_foreign_bundle_does_not_fill_case(self):
        summary, _, data = self.run_stage([self.bundle('gpu', quality_digest='0' * 64), self.bundle('npu')])
        self.assertEqual(summary['alignment_status'], 'insufficient_evidence')
        self.assertEqual(data['cases'][0]['result'], 'insufficient_evidence')
        self.assertTrue(data['uncovered'])

    def test_native_rerun_only_with_bound_gpu_vectors(self):
        protocol = {'dataset': 'exam', 'model_identity': 'run1'}
        projection = self.put('projection.json', {'protocol': protocol, 'hardware': 'npu',
                              'format': 'task-quality-native-v1', 'scores': []})
        receipt = self.put('receipt.json', {'status': 'completed', 'task_id': 'verify-agent-plus',
                           'conclusions': {'evaluation_validity': 'pass'}, 'detailed_report': {'hardware': 'npu'},
                           'evidence': {'quality_evidence': projection}})
        self.report = self.quality(self.rows, {'receipt': receipt, 'measurement': projection})
        native = {'invalid_only_for_test': True, 'protocol': protocol}
        summary, _, output = self.run_stage(native=native)
        self.assertNotIn('npu_measurement', output)
        bundle = self.bundle('gpu')
        summary, _, output = self.run_stage([bundle], native=native)
        self.assertEqual(output['npu_measurement']['status'], 'unavailable')
        self.assertEqual(summary['alignment_status'], 'insufficient_evidence')
        path = Path(summary['manifest']).parent
        self.assertTrue((path / 'token-run.request.json').exists())
        drift = {**native, 'protocol': {**protocol, 'model_identity': 'other_model'}}
        summary, _, output = self.run_stage([bundle], native=drift)
        self.assertEqual(output['npu_measurement']['status'], 'unavailable')
        self.assertFalse((Path(summary['manifest']).parent / 'token-run.request.json').exists())
        (self.directory / 'gpu.json').write_text('{}')
        _, _, output = self.run_stage([bundle], native=native)
        self.assertNotIn('npu_measurement', output)

    def test_full_failed_subject_roster_from_bound_npu_source(self):
        self.rows[0].pop('sample_comparison')
        projection = self.put('measurement.json', {'protocol': {'dataset': 'exam'}, 'hardware': 'npu',
                              'format': 'task-quality-native-v1', 'scores': []})
        receipt = self.put('measured.json', {'status': 'completed', 'task_id': 'verify-agent-plus',
                           'conclusions': {'evaluation_validity': 'pass'}, 'detailed_report': {
                           'hardware': 'npu', 'stable_samples': {'math': [3, 4, 5]}},
                           'evidence': {'quality_evidence': projection}})
        self.report = self.quality(self.rows, {'receipt': receipt, 'measurement': projection})
        _, selected, _ = self.run_stage([self.bundle('gpu')])
        self.assertEqual([case['sample_id'] for case in selected['cases']], [3, 4, 5])
        self.rows[0]['subset'] = None
        self.report = self.quality(self.rows, {'receipt': receipt, 'measurement': projection})
        _, selected, _ = self.run_stage()
        self.assertFalse(selected['cases'])
        self.assertIsNone(selected['pending_scopes'][0]['subset'])

    def test_expands_original_gpu_reviews_without_declared_sample_results(self):
        import importlib.util
        source = Path(__file__).resolve().parents[1] / 'task_quality/test_compare.py'
        spec = importlib.util.spec_from_file_location('quality_fixture', source)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        quality = fixture.QualityComparison()
        quality.setUp()
        self.addCleanup(quality.doCleanups)
        quality.entry['scores'][0]['target'] = {'max_drop': 0}
        gpu = [{'subset': 'a', 'sample_id': 18, 'score': 1}, {'subset': 'b', 'sample_id': 18, 'score': 0}]
        npu = [{**row, 'score': 1 - row['score']} for row in gpu]
        quality.gpu['scores'][0]['samples'] = gpu
        quality.npu['scores'][0]['samples'] = npu
        report = quality.compare()
        self.assertEqual(report['conclusions']['precision_target'], 'fail')
        self.assertNotIn('sample_comparison', report['scores'][0])
        self.report = self.put('original-quality.json', report)
        _, selected, _ = self.run_stage()
        self.assertEqual([(r['subset'], r['sample_id']) for r in selected['cases']], [('a', 18)])
        self.assertFalse(selected['uncovered'])

    def test_cpu_reference_reuses_original_protocol_model_identity(self):
        files = {'model.safetensors': 'c' * 64}
        identity = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        model = {'root': str(self.directory), 'files': files, 'weight_files': list(files)}
        handoff = self.put('handoff.json', {'model': model})
        deployment = self.put('deployment.json', {'task_id': 'verify-agent', 'status': 'completed', 'success': True,
                              'inputs': {'model': model}, 'evidence': {'handoff': handoff['path']},
                              'detailed_report': {'handoff_sha256': handoff['sha256']}})
        original = {'measurement': {'deployment': deployment}, 'protocol': {'model_identity': identity}}
        measurement = self.put('native.json', {'hardware': 'npu', 'format': 'task-quality-native-v1',
                              'protocol': {'dataset': 'exam', 'model_identity': identity,
                                           'generation_config': {'temperature': 0}},
                              'static_conditions': {'server_generation': {'overrides': {
                              '--generation-config': 'vllm'}, 'model_generation_files': {}}}, 'scores': []})
        receipt = self.put('native-receipt.json', {'task_id': 'verify-agent-plus', 'status': 'completed',
                           'inputs': original, 'detailed_report': {'hardware': 'npu'},
                           'conclusions': {'evaluation_validity': 'pass'}, 'evidence': {'quality_evidence': measurement}})
        self.report = self.quality(self.rows, {'measurement': measurement, 'receipt': receipt})
        summary, _, _ = self.run_stage([self.bundle('gpu'), self.bundle('npu')])
        req = json.loads((Path(summary['manifest']).parent / 'case-0/request.json').read_text())
        self.assertEqual(req['cpu_model']['model_identity'], identity)
        self.assertTrue(req['cpu_model']['plain_greedy'])
