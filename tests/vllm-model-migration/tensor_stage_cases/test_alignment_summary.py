"""离线构造绑定的报告，验证跨题汇总、范围和证据隔离。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/scripts'
sys.path.insert(0, str(SCRIPTS))
from coordination.runtime import sources
from customer_workflow.alignment_summary import summarize
from customer_workflow.cli import plan


class AlignmentSummaryTests(unittest.TestCase):
    def put(self, value):
        path = self.root / f'{len(list(self.root.iterdir()))}.json'
        path.write_text(json.dumps(value))
        return sources.ref(path)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.policy = {'atol': 1, 'rtol': 0}
        self.accuracy = {'token_alignment': {}, 'tensor_alignment': self.policy}
        self.quality = self.put({'baseline': {'dataset': 'exam'}})
        self.source = {'dataset': 'exam', 'report': self.quality, 'precision_target': 'fail'}
        self.value = {'status': 'completed', 'run_id': 'offline', 'task_quality_conclusion': {
            'precision_target': 'fail', 'summary': '任务已完成，任务质量未达标。', 'reports': [self.source]}}
        self.cases = [{'subset': 'math', 'sample_id': n, 'triggers': [{'metric': 'acc'}, {'metric': 'score'}]}
                      for n in (17, 31)]
        self.kinds = {'gpu': 'runtime', 'npu': 'runtime'}  # 固定回执输入，不运行设备。
        self.tokens(['mismatch', 'mismatch'])

    def token_row(self, case, outcome):
        row = {**case, 'result': outcome, 'evidence_kind': self.kinds}
        if outcome == 'insufficient_evidence':
            return {**row, 'reason': '缺 GPU 预测记录'}
        pair = {side: self.put({'side': side, **case}) for side in self.kinds}
        request = self.put({'case': case, **pair, 'token_alignment': {}})
        report = {**row, 'status': 'completed', 'operation': 'token', 'task_id': 'vllm-precision-compare',
                  'validity': 'not_evaluated' if outcome == 'input_mismatch' else 'valid', 'first_anomalous_token': 2,
                  'evidence': {**pair, 'request': request}, 'reason': '输入不同',
                  'differences': {'raw_logits': {'atol': 0}}, 'covered_tokens': 2}
        return {**row, 'first_anomalous_token': 2, 'comparison': self.put(report)}

    def tokens(self, outcomes, pending=()):
        self.rows = [self.token_row(case, outcome) for case, outcome in zip(self.cases, outcomes)]
        selection = self.put({'format': 'token-case-selection-v1', 'dataset': 'exam', 'quality_report': self.quality,
                              'cases': self.cases, 'pending_scopes': list(pending)})
        self.token = self.put({'task_id': 'vllm-precision-align', 'operation': 'token', 'quality_report': self.quality,
                               'selection': selection, 'cases': self.rows, 'alignment_status': 'insufficient_evidence'})
        self.value['token_alignment'] = [{'quality_report': self.quality, 'token_report': self.token}]

    def tensors(self, outcome='mismatch', missing_peer=True, extra_planned_slot=None):
        rows = []
        for case, token in zip(self.cases, self.rows):
            key = {k: case[k] for k in ('subset', 'sample_id')}
            slots = [{'id': 'y'}]
            if not rows and extra_planned_slot:
                slots.append({'id': extra_planned_slot})
            plan = self.put({'format': 'tensor-plan-v1', 'case': key, 'position': 2,
                             'calls': [{'id': 'add0', 'slots': slots}], 'quality_report': self.quality,
                             'comparison': token['comparison']})
            row = {**key, 'position': 2, 'plan': plan, 'source_result': 'mismatch', 'result': outcome}
            if rows and missing_peer:
                rows.append({**row, 'result': 'awaiting_gpu', 'reason': '等待 GPU 张量包'})
                continue
            pair = {side: self.put({'plan': plan, 'side': side}) for side in self.kinds}
            call = {'id': 'add0', 'slots': [{'slot': 'y', 'result': 'fail' if outcome == 'mismatch' else 'pass'}]}
            report = {'task_id': 'vllm-precision-compare', 'status': 'completed', 'operation': 'tensor',
                      'case': key, 'position': 2, 'plan': plan, 'tensor_alignment': self.policy,
                      'result': outcome, 'evidence': {**pair, 'plan': plan}, 'calls': [call],
                      'capture_status': 'complete', 'scope_uncovered': [], 'evidence_kind': self.kinds,
                      'cpu_check': {'common_reference': 'not_evaluated'}, 'target_status': 'fail' if outcome == 'mismatch' else 'pass'}
            rows.append({**row, **pair, 'reproduction': {'status': 'reproduced'}, 'comparison': self.put(report)})
        self.tensor = self.put({'task_id': 'vllm-precision-align', 'operation': 'tensor', 'quality_report': self.quality,
                                'token_report': self.token, 'cases': rows, 'comparison_result': 'partially_evaluated'})
        self.value['tensor_alignment'] = [{'quality_report': self.quality, 'token_report': self.token,
                                            'tensor_report': self.tensor}]

    def summary(self):
        return summarize(self.value, self.accuracy, intent_known=True)

    def test_token_mismatch_and_missing(self):
        self.tokens(['mismatch', 'insufficient_evidence'], [{'metric': 'other', 'reason': '失败分项尚无名册'},
                                                            {'metric': 'partial'}])
        layer = self.summary()['token_prediction']
        self.assertEqual(layer['result'], 'mismatch')
        self.assertEqual(layer['coverage']['known'], 2)
        self.assertIsNone(layer['coverage']['expected'])
        self.assertIn('第 2 个输出位置', layer['summary'])
        self.assertIn('缺 GPU 预测记录', layer['summary'])
        self.assertIn('失败分项尚无名册', layer['summary'])
        self.assertIn('未定界的分项缺少原因', str(layer['gaps']))
        self.assertEqual(layer['cases'][0]['triggers'], self.cases[0]['triggers'])

    def test_tensor_mismatch_and_missing(self):
        self.tensors()
        layer = self.summary()['operator_tensor']
        self.assertEqual(layer['result'], 'mismatch')
        self.assertEqual(layer['coverage']['expected'], 2)
        for text in ('add0.y', '等待 GPU 张量包', '31'):
            self.assertIn(text, layer['summary'])

    def test_tensor_case_with_both_failed_and_unassessed_slots(self):
        self.tensors(missing_peer=False, extra_planned_slot='weight')
        layer = self.summary()['operator_tensor']
        self.assertEqual(layer['result'], 'mismatch')
        self.assertEqual(layer['coverage']['cases_with_gaps'], 1)
        self.assertIn('add0.y', layer['summary'])
        self.assertIn('add0.weight', layer['summary'])

    def test_aligned_child_missing_a_planned_slot_does_not_pass(self):
        self.tensors('aligned', missing_peer=False, extra_planned_slot='weight')
        layer = self.summary()['operator_tensor']
        self.assertEqual(layer['result'], 'insufficient_evidence')
        self.assertEqual(layer['coverage']['by_result']['aligned'], 1)

    def test_no_common_cpu_can_still_report_device_alignment(self):
        self.tensors('aligned', missing_peer=False)
        layer = self.summary()['operator_tensor']
        self.assertEqual((layer['result'], layer['target_status']), ('aligned', 'pass'))
        self.assertIn('共同 CPU 参考不足', layer['summary'])
        self.assertNotIn('hardware_target_status', layer)

    def test_damaged_token_comparison_only_invalidates_its_case(self):
        self.tensors()
        Path(self.rows[1]['comparison']['path']).write_text('{}')
        result = self.summary()
        self.assertEqual(result['token_prediction']['result'], 'mismatch')
        self.assertEqual(result['operator_tensor']['result'], 'mismatch')
        self.assertIn('SHA256', str(result['operator_tensor']['gaps']))

    def test_malformed_nested_tensor_result_keeps_confirmed_peer(self):
        self.tensors(missing_peer=False)
        report = sources.bound(self.tensor)
        row = report['cases'][1]
        row['comparison'] = self.put({**sources.bound(row['comparison']), 'calls': None})
        self.value['tensor_alignment'][0]['tensor_report'] = self.put(report)
        layer = self.summary()['operator_tensor']
        self.assertEqual(layer['result'], 'mismatch')
        self.assertEqual(layer['coverage']['by_result']['insufficient_evidence'], 1)
        self.assertEqual(layer['gaps'][0]['sample_id'], 31)

    def test_absent_cpu_and_unspecified_sources_preserve_direct_comparison(self):
        self.kinds = {'gpu': 'unspecified', 'npu': 'runtime'}
        self.tokens(['mismatch', 'mismatch'])
        self.tensors('aligned', missing_peer=False)
        report = sources.bound(self.tensor)
        row = report['cases'][1]
        row['comparison'] = self.put({**sources.bound(row['comparison']), 'cpu_check': None})
        self.value['tensor_alignment'][0]['tensor_report'] = self.put(report)
        layer = self.summary()['operator_tensor']
        self.assertEqual((layer['result'], layer['target_status']), ('aligned', 'pass'))
        self.assertFalse(layer['gaps'])

    def test_mixed_simulated_difference_and_missing_record(self):
        self.kinds = {'gpu': 'synthetic', 'npu': 'runtime'}
        self.tokens(['mismatch', 'insufficient_evidence'])
        layer = self.summary()['token_prediction']
        self.assertEqual(layer['result'], 'simulated')
        self.assertIn('模拟差异 1 题', layer['summary'])
        self.assertIn('缺 GPU 预测记录', layer['summary'])

    def test_changed_token_tolerance_does_not_reuse_old_verdict(self):
        self.accuracy['token_alignment'] = {'raw_logits': {'atol': 99}, 'processed_scores': {'atol': 99}}
        result = self.summary()
        self.assertEqual(result['token_prediction']['result'], 'insufficient_evidence')
        self.assertIn('容差', result['token_prediction']['summary'])

    def test_uncertain_source_and_unresolved_target_keep_their_reasons(self):
        self.kinds = {'npu': 'runtime', 'gpu': 'unspecified'}
        self.tokens(['aligned', 'aligned'])
        self.source['scores'] = [{'metric': 'recall', 'target': {'max_drop': 1}, 'precision_target': 'not_evaluated'}]
        layer = self.summary()['token_prediction']
        self.assertEqual(layer['result'], 'insufficient_evidence')
        self.assertEqual(layer['coverage']['cases_with_gaps'], 2)
        self.assertIsNone(layer['coverage']['expected'])
        self.assertIn('来源', layer['summary'])
        self.assertIn('recall', layer['summary'])

    def test_input_mismatch_is_incomparable(self):
        self.tokens(['aligned', 'input_mismatch'])
        result = self.summary()
        self.assertEqual(result['token_prediction']['coverage']['by_result']['input_mismatch'], 1)
        self.assertEqual(result['operator_tensor']['result'], 'insufficient_evidence')

    def test_scope_and_static_plan(self):
        self.source['precision_target'] = 'pass'
        self.assertEqual(self.summary()['token_prediction']['result'], 'not_triggered')
        self.source['precision_target'] = 'not_evaluated'
        self.assertIsNone(self.summary()['token_prediction']['coverage']['expected'])
        self.accuracy.clear()
        self.value.pop('token_alignment')
        self.assertEqual(self.summary()['operator_tensor']['result'], 'not_requested')
        task = {'model': {}, 'environment': {}, 'accuracy': {'evaluator': 'evalscope'}}
        self.value['status'] = 'running'
        self.assertEqual(self.summary()['operator_tensor']['result'], 'pending')
        from customer_workflow.contract import normalize
        result = plan(normalize(task), self.root)['alignment_summary']
        self.assertEqual(result['token_prediction']['result'], 'not_run')
