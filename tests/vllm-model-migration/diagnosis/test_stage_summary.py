"""普通精度阶段发布与总入口验收，评测使用离线原生回执，定界使用本机模拟服务。"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path[:0] = [str(PACKAGE / 'scripts'), str(PACKAGE / 'skills/vllm-precision-align/scripts'),
                str(Path(__file__).resolve().parent)]
from alignment import cli, evidence, runner, summary
from coordination.runtime import receipts
from mock_vllm import MockVLLM


class StageSummaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.parent = self.root / 'run-1' / 'vllm-precision-align'
        self.request = {'schema_version': '1.0', 'evaluations': []}
        self.eval_input = {'targets': {'accuracy': 1}, 'baseline': {'accuracy': 1}}
        self.request['evaluations'].append({'id': 'eval-1', 'request': self.file('request.json', self.eval_input)})
        self.attempt, self.stage = evidence.begin(self.request, self.parent, 'run-1', 'run')

    def file(self, name, value):
        path = self.root / name
        evidence.write(path, value)
        return evidence.ref(path)

    def evaluation(self, target='pass', anomalies=()):
        decisions = {'evaluation_validity': 'pass', 'comparability': 'pass', 'precision_target': target}
        raw = {'task_id': 'verify-agent-plus', 'schema_version': '1.0', 'run_id': 'run-1',
               'operation': 'run', 'inputs': self.eval_input, 'status': 'completed', 'success': target == 'pass',
               'conclusions': decisions, 'uncovered': [], 'anomalies': list(anomalies),
               'evidence': {'request': self.request['evaluations'][0]['request']['path']},
               'detailed_report': {'measurements': [{'valid': True, 'metrics': {'accuracy': 0.78}}]}}
        source = self.file('evaluation.json', raw)
        self.stage['tasks']['evaluations']['eval-1'].update(status='completed', receipt=source,
                conclusions=decisions, baseline_requested=True, targets_requested=True)
        summary.collect_result(raw, self.stage, 'eval-1', source)

    def automatic(self):
        runner.auto_diagnoses(self.request, self.root, self.attempt, self.stage, 10, 'run')

    def receive(self, code, terminal):
        return receipts.receipt(json.dumps(terminal), code, 'precision', 'vllm-precision-align',
                                self.request, self.parent, 'run-1', 'run')[0]

    def finish(self, expected_code):
        summary.conclude(self.stage)
        code, terminal = runner.finish(self.attempt, self.stage)
        self.assertEqual(code, expected_code)
        stage = self.receive(code, terminal)
        self.assertEqual(evidence.bound(stage['handoff'])['conclusions'], terminal['conclusions'])
        return stage

    def anomaly(self, endpoint):
        return {'sample_id': 17, 'anomaly_type': 'empty_tool_calls',
                'request': {'messages': [{'role': 'user', 'content': '北京今天天气怎么样'}],
                            'tools': [{'type': 'function', 'function': {'name': 'get_weather'}}],
                            'tool_choice': 'auto'}, 'service': {'endpoint': endpoint, 'model': 'Qwen3.5-27B'}}

    def test_automatic_through_publication_and_entry(self):
        with MockVLLM() as server:
            for target, code in [('pass', 0), ('fail', 1)]:
                with self.subTest(target=target):
                    def measured(request, values, attempt, manifest, timeout, action):
                        self.stage = manifest
                        self.evaluation(target, [self.anomaly(server.endpoint)])

                    stdout = io.StringIO()
                    source = self.file('stage.json', self.request)
                    with patch.object(runner, 'evaluations', measured), redirect_stdout(stdout):
                        rc = cli.main(['--request', source['path'], '--run-id', 'run-1',
                                       '--work-root', str(self.root)])
                    self.assertEqual(rc, code)
                    stage = self.receive(code, json.loads(stdout.getvalue()))
                    self.assertEqual(set(stage['results']), {'eval-1'})
                    self.assertEqual(stage['conclusions'], {'precision_testing': 'pass', 'evaluation_validity': 'pass',
                        'comparability': 'pass', 'precision_target': target, 'demarcation': 'pass'})
                    row = stage['tasks']['diagnoses']['auto-eval-1-17-empty_tool_calls']
                    self.assertEqual(evidence.bound(row['receipt'])['detailed_report']['primary'], 'TOOL_PARSER')

    def test_optional_timeout_does_not_turn_blocked_evaluation_into_failure(self):
        self.evaluation(anomalies=[self.anomaly('http://localhost:8000')])
        self.request['evaluations'].append({'id': 'waiting', 'blocked_reason': '缺数据'})
        self.stage['tasks']['evaluations']['waiting'] = {'status': 'blocked', 'reason': '缺数据'}
        with patch.object(runner, 'demarcate_anomaly', return_value=(1, {
                'status': 'failed', 'diagnostic': {'message': '定界超时'}})):
            self.automatic()
        stage = self.finish(3)
        self.assertEqual(stage['status'], 'blocked')
        self.assertEqual(stage['conclusions']['demarcation'], 'not_evaluated')
        self.assertIn('定界超时', str(stage['tasks']['diagnoses']))

    def test_legacy_and_empty_diagnosis_ledger(self):
        self.evaluation()
        self.assertEqual(self.finish(0)['conclusions']['demarcation'], 'not_evaluated')
        del self.stage['tasks']['diagnoses']
        del self.stage['conclusions']['demarcation']
        code, terminal = runner.finish(self.attempt, self.stage)
        self.receive(code, terminal)


    def test_mixed_diagnoses(self):
        with MockVLLM() as good, MockVLLM(empty_completion=True) as empty:
            for kind, expected in [('inconclusive', 'review_required'), ('timeout', 'not_evaluated')]:
                with self.subTest(kind=kind):
                    self.attempt, self.stage = evidence.begin(self.request, self.parent, 'run-1', 'run')
                    first = self.anomaly(good.endpoint)
                    second = dict(self.anomaly(empty.endpoint), sample_id=18)
                    self.evaluation(anomalies=[first, second])
                    demarcate = runner.demarcate_anomaly

                    def execute(anomaly, *args):
                        if kind == 'timeout' and anomaly['sample_id'] == 18:
                            return 1, {'status': 'failed', 'diagnostic': {'message': '定界超时'}}
                        return demarcate(anomaly, *args)

                    with patch.object(runner, 'demarcate_anomaly', execute):
                        self.automatic()
                    stage = self.finish(0)
                    self.assertEqual(stage['conclusions']['evaluation_validity'], 'pass')
                    self.assertEqual(stage['conclusions']['demarcation'], expected)
                    rows = stage['tasks']['diagnoses']
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(rows['auto-eval-1-17-empty_tool_calls']['conclusions']['demarcation'], 'pass')
                    self.assertEqual(rows['auto-eval-1-18-empty_tool_calls']['conclusions']['demarcation'], expected)

    def test_entry_rejects_corrupt_files_and_mixed_evaluation(self):
        with MockVLLM() as server:
            self.evaluation(anomalies=[self.anomaly(server.endpoint)])
            self.automatic()
        stage = self.finish(0)
        terminal = {'status': stage['status'], 'conclusions': stage['conclusions'], 'manifest': stage['evidence']['manifest']}
        row = next(iter(stage['tasks']['diagnoses'].values()))
        for path in [row['receipt']['path'], stage['evidence']['ledger'], stage['handoff']['path']]:
            with self.subTest(path=Path(path).name):
                path = Path(path)
                original = path.read_bytes()
                path.write_bytes(original.replace(b'completed', b'failed'))
                with self.assertRaises(ValueError):
                    self.receive(0, terminal)
                path.write_bytes(original)
        stage['results']['wrong-group'] = row
        code, terminal = runner.finish(self.attempt, stage)
        with self.assertRaisesRegex(ValueError, '结果范围'):
            self.receive(code, terminal)

    def test_manual_diagnosis_shares_evaluation_id(self):
        with MockVLLM() as server:
            badcase = self.file('manual.json', self.anomaly(server.endpoint)['request'])
            item = {'id': 'eval-1', 'badcase': badcase, 'deployment': 'svc'}
            self.request['diagnoses'] = [item]
            self.stage['tasks']['diagnoses']['eval-1'] = {'status': 'not_run'}
            deployment = self.deployment(server.endpoint)
            self.request['deployments'] = {'svc': self.file('verify.request.json', deployment['inputs'])}
            self.stage['tasks']['deployments']['svc'] = {'status': 'completed', 'conclusions': deployment['conclusions'],
                                                        'receipt': self.file('verify.json', deployment)}
            self.evaluation()
            runner.diagnoses(self.request, self.root, self.attempt, self.stage, 10, 'run')
        stage = self.finish(0)
        evaluation = stage['results']['eval-1']['receipt']
        diagnosis = stage['tasks']['diagnoses']['eval-1']['receipt']
        self.assertEqual(evidence.bound(evaluation)['task_id'], 'verify-agent-plus')
        self.assertEqual(evidence.bound(diagnosis)['inputs']['badcase'], badcase)

    def deployment(self, endpoint):
        handoff = self.file('deployment.json', {'endpoint': endpoint, 'model': {'name': 'Qwen3.5-27B'},
                            'final_service': {'state': 'retained'}})
        response = self.file('response.json', {'choices': []})
        case = {'id': 'case', 'kind': 'chat', 'required': True}
        observed = {**case, 'response': {**response, 'raw_path': response['path'], 'raw_sha256': response['sha256']}}
        cases = self.file('cases.json', [observed])
        return {'schema_version': '1.0', 'task_id': 'verify-agent', 'run_id': 'run-1', 'operation': 'verify',
                'status': 'completed', 'success': True, 'inputs': {'service': {'ownership': 'borrowed'}, 'cases': [case]},
                'conclusions': {'lifecycle': 'pass'}, 'evidence': {'handoff': handoff['path'], 'cases': cases['path']},
                'detailed_report': {'handoff_sha256': handoff['sha256'], 'cases': [observed]}, 'uncovered': []}

    def test_auto_disabled_with_detected_anomaly(self):
        self.request['auto_diagnose'] = False
        self.evaluation(anomalies=[self.anomaly('http://localhost:8000')])
        with patch.object(runner, 'demarcate_anomaly') as child:
            self.automatic()
        child.assert_not_called()
        stage = self.finish(0)
        self.assertEqual(stage['tasks']['diagnoses'], {})
        self.assertEqual(stage['conclusions']['demarcation'], 'not_evaluated')

    def test_entry_rejects_missing_diagnosis_or_different_sample(self):
        with MockVLLM() as server:
            first = self.anomaly(server.endpoint)
            second = {**first, 'request': {**first['request'], 'temperature': 0}}
            self.evaluation(anomalies=[first, second])
            self.automatic()
        stage = self.finish(0)
        rows = stage['tasks']['diagnoses']
        self.assertEqual(len(rows), 2)
        one, two = rows.values()
        # 同一次评测中的同题号记录也必须保留各自原始请求。
        one['receipt'] = two['receipt']
        code, terminal = runner.finish(self.attempt, stage)
        with self.assertRaisesRegex(ValueError, '异常样本'):
            self.receive(code, terminal)
        rows.clear()
        stage['conclusions']['demarcation'] = 'not_evaluated'
        code, terminal = runner.finish(self.attempt, stage)
        with self.assertRaisesRegex(ValueError, '异常范围'):
            self.receive(code, terminal)
