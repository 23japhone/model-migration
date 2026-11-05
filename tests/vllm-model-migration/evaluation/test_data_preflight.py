"""无推理预检、完整性诊断与阶段失败隔离。"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path[:0] = [str(ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'),
               str(ROOT / 'skills/vllm-precision-align/scripts'), str(ROOT / 'scripts')]


class DataPreflightTests(unittest.TestCase):
    def test_live_progress_requires_a_forwarded_inference_request(self):
        import http.client
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from urllib.parse import urlsplit
        from precision.runtime.capture import recording
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.reply()
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.reply()
            def reply(self):
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers()
                self.wfile.write(b'{}')
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True); thread.start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve() / 'capture'
            with recording('http://127.0.0.1:' + str(upstream.server_port), root, 5, lambda raw, status: []) as endpoint:
                address = urlsplit(endpoint)
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                connection.request('GET', '/v1/models'); connection.getresponse().read()
                self.assertFalse((root / 'progress.json').exists())
                connection.request('POST', '/v1/chat/completions', body='{}'); connection.getresponse().read()
                connection.close()
            self.assertEqual(json.loads((root / 'progress.json').read_text())['phase'], 'evaluating')
            self.assertEqual(len(json.loads((root / 'index.json').read_text())['requests']), 2)

    def test_public_action_and_plan_are_available_without_loading_evaluator(self):
        from precision.cli import parser
        args = parser().parse_args(['--action', 'preflight', '--plan'])
        self.assertEqual(args.action, 'preflight')
        self.assertTrue(args.plan)

    def test_preflight_success_is_not_an_accuracy_measurement(self):
        from precision import preflight
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            request = {'evaluator': {'backend': 'evalscope'}, 'targets': []}
            inputs = {'protected': [], 'asset': {}, 'handoff': {}}
            result = {'status': 'completed', 'tasks': [{'dataset': 'one', 'count': 15}], 'errors': []}
            with patch.object(preflight, 'inputs', return_value=inputs), \
                    patch.object(preflight, 'check', return_value=result), \
                    patch('precision.runtime.service.snapshot', side_effect=AssertionError('no model service')), \
                    patch('precision.runner.measure_tasks', side_effect=AssertionError('no inference')):
                code, report = preflight.run(request, root, 'preflight')
            self.assertEqual((code, report['status'], report['operation']), (0, 'completed', 'preflight'))
            self.assertEqual(report['conclusions']['evaluation_validity'], 'not_evaluated')
            self.assertEqual(report['detailed_report']['data_preflight'], result)
            self.assertEqual(json.loads(Path(report['report_path']).read_text()), report)

    def test_run_blocks_before_service_snapshot_when_data_preflight_fails(self):
        from precision import runner
        request = {'evaluator': {'backend': 'evalscope'}}
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'collect', return_value={
                'protected': [], 'hardware': 'npu'}), patch('precision.preflight.check',
                return_value={'status': 'blocked', 'errors': [{'code': 'dataset_path_conflict', 'message': 'bad path'}]}), \
                patch('precision.runtime.service.snapshot') as service:
            code, report = runner.run(request, Path(folder).resolve(), 'blocked')
        self.assertEqual((code, report['status']), (3, 'blocked'))
        service.assert_not_called()
        self.assertIn('dataset_path_conflict', report['diagnostic']['message'])

    def test_cancelled_preflight_persists_terminal_state_without_accuracy_claims(self):
        from precision import preflight
        for error in (KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as folder, \
                    patch.object(preflight, 'inputs', return_value={'protected': []}), \
                    patch.object(preflight, 'check', side_effect=error):
                code, report = preflight.run({'evaluator': {'backend': 'evalscope'}}, Path(folder).resolve(), 'cancelled')
                self.assertEqual((code, report['status'], report['success']), (1, 'failed', False))
                self.assertEqual(json.loads(Path(report['report_path']).read_text()), report)
                self.assertTrue(all(value == 'not_evaluated' for value in report['conclusions'].values()))
                self.assertEqual(report['detailed_report']['data_preflight']['errors'][0]['code'], 'dataset_preflight_cancelled')

    def test_extra_file_is_reported_with_its_name(self):
        from precision.runtime.evalscope.samples import check_directory
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'test.jsonl').write_text('data')
            (root / 'test_socratic.jsonl').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'dataset_unlisted_file.*test_socratic.jsonl'):
                check_directory(root, root, [{'path': 'test.jsonl'}])

    def test_missing_ceval_metadata_has_actionable_diagnostic(self):
        from precision.runtime.evalscope.data import metadata_ready
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'dataset_metadata_missing.*README.md'):
                metadata_ready('ceval', folder)


class StagePreflightTests(unittest.TestCase):
    def test_all_preflights_finish_before_any_measurement_and_failures_stay_local(self):
        from alignment import runner
        items = [{'id': key} for key in ('bad-a', 'good', 'bad-b')]
        requests = {item['id']: {'evaluator': {'backend': 'evalscope'}, 'tasks': []} for item in items}
        manifest = {'tasks': {'evaluations': {item['id']: {'status': 'not_run'} for item in items}},
                    'resources': [], 'run_id': 'fixture'}
        events = []
        def delegate(group, key, request, attempt, run_id, timeout, action):
            events.append((action, key))
            status = 'completed' if key == 'good' else 'blocked'
            return {'status': status, 'conclusions': {}, 'detailed_report': {'data_preflight': {'status': status}}}, {'path': key}
        def evaluate(item, *args):
            events.append(('run', item['id']))
            return None, None
        with tempfile.TemporaryDirectory() as folder, patch('alignment.preflight.delegate', side_effect=delegate), \
                patch.object(runner, 'evaluate_item', side_effect=evaluate), \
                patch.object(runner, 'save'), patch('alignment.preflight.save'):
            runner.evaluations({'evaluations': items}, {'evaluations': requests}, Path(folder), manifest, 60, 'run')
        self.assertEqual(events, [('preflight', 'bad-a'), ('preflight', 'good'), ('preflight', 'bad-b'), ('run', 'good')])
        self.assertEqual(manifest['tasks']['evaluations']['bad-a']['status'], 'blocked')
        self.assertEqual(manifest['tasks']['evaluations']['bad-b']['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
