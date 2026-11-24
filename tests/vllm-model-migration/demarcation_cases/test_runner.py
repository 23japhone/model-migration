"""三步定界编排与假服务集成测试（不依赖真机）。"""
import hashlib
import http.server
import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align/skills/three-step-demarcation/scripts'
sys.path.insert(0, str(SCRIPTS))

from demarcation import runner  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def _respond(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length) if length else b''
        payload = json.loads(body) if body else None
        entry = self.routes.get((self.command, self.path))
        if entry is None:
            self.send_response(404)
            self.end_headers()
            return
        status, data = entry(payload) if callable(entry) else entry
        raw = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def log_message(self, *args):
        pass


class MockServer:
    def __init__(self, routes):
        _Handler.routes = routes
        self._server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self):
        host, port = self._server.server_address
        return 'http://%s:%d' % (host, port)

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def _write_badcase(directory, value):
    path = directory / 'badcase.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class RunnerTests(unittest.TestCase):
    def _request(self, endpoint, badcase, render_mode='auto'):
        return {'schema_version': '1.0',
                'service': {'endpoint': endpoint, 'model': None},
                'badcase': badcase,
                'render_mode': render_mode,
                'timeout_seconds': 30}

    def _routes(self):
        return {
            ('GET', '/version'): (200, {'version': '0.20.2'}),
            ('GET', '/v1/models'): (200, {'data': [{'id': 'mock-model'}]}),
            ('POST', '/v1/chat/completions/render'): (200, {'token_ids': [1, 2, 3]}),
            ('POST', '/detokenize'): (200, {'prompt': '你好'}),
            ('POST', '/v1/completions'): (200, {'choices': [{'text': '北京天气晴', 'finish_reason': 'stop'}]}),
            ('POST', '/v1/chat/completions'): (200, {'choices': [{'message': {'content': '北京天气晴'}, 'finish_reason': 'stop'}]}),
        }

    def test_full_run_produces_pass(self):
        server = MockServer(self._routes())
        try:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                badcase = _write_badcase(base, {'messages': [{'role': 'user', 'content': '你好'}]})
                code, manifest = runner.run(self._request(server.endpoint, badcase), base / 'runs', 'run-1')
            self.assertEqual(manifest['status'], 'completed')
            self.assertEqual(manifest['conclusions']['demarcation'], 'pass')
            self.assertEqual(manifest['detailed_report']['primary'], 'PASS')
            self.assertEqual(code, 0)
        finally:
            server.close()

    def test_fatal_empty_messages_is_request_without_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            badcase = _write_badcase(base, {'messages': []})
            code, manifest = runner.run(self._request('http://127.0.0.1:1', badcase), base / 'runs', 'run-2')
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(manifest['detailed_report']['primary'], 'REQUEST')
        self.assertEqual(code, 0)

    def test_render_fallback_to_tokenize(self):
        routes = self._routes()
        routes[('POST', '/v1/chat/completions/render')] = (404, {'error': 'not found'})
        routes[('POST', '/tokenize')] = (200, {'tokens': [1, 2, 3]})
        server = MockServer(routes)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                badcase = _write_badcase(base, {'messages': [{'role': 'user', 'content': '你好'}]})
                code, manifest = runner.run(self._request(server.endpoint, badcase), base / 'runs', 'run-3')
            meta = manifest['detailed_report']['meta']
            self.assertEqual(meta['render_mode'], 'tokenize')
            self.assertIn('render 不可用', meta['render_fallback_chain'])
        finally:
            server.close()

    def test_step3_failure_is_inconclusive_without_retry(self):
        routes = self._routes()
        routes[('POST', '/v1/completions')] = (400, {'error': 'return_token_ids unsupported'})
        server = MockServer(routes)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                badcase = _write_badcase(base, {'messages': [{'role': 'user', 'content': '你好'}]})
                code, manifest = runner.run(self._request(server.endpoint, badcase), base / 'runs', 'run-4')
            self.assertEqual(manifest['status'], 'completed')
            self.assertEqual(manifest['detailed_report']['primary'], 'INCONCLUSIVE')
            self.assertEqual(manifest['conclusions']['demarcation'], 'review_required')
        finally:
            server.close()


if __name__ == '__main__':
    unittest.main()
