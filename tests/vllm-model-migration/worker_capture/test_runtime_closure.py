"""采集工具公开闭环；独立于 GPU 基线和任何评测 SDK。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worker_capture import native_fixture as native
from worker_capture.session_fixture import AGENT, BUNDLE, deployment, isolated_bundle, public, reference, write

VERIFY = BUNDLE / AGENT / 'scripts/verify.py'


class RuntimeClosure(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_session_public_plan_without_baseline_or_sdk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();path=root/'request.json'
            path.write_text(json.dumps({'operation':'begin','source':{'path':'/missing/report.json','sha256':'0'*64},
                                       'owner_pid':123}))
            result=subprocess.run([sys.executable,'-B',str(VERIFY),'--request',str(path),
                '--work-root',str(root/'out'),'--run-id','capture','--action','capture-session','--plan'],
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'out').exists())

    def start(self):
        self.verify = isolated_bundle(self.root)
        self.calls = []
        fixture = self
        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = self.rfile.read(int(self.headers['Content-Length']))
                fixture.calls.append(payload)
                response = native.respond(fixture.runtime, self.headers['X-Precision-Trace'], fixture.tokens)
                raw = json.dumps(response).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.tokens = [71, 92]
        self.report, self.runtime = deployment(self.root, f'http://127.0.0.1:{self.server.server_port}')
        self.session = self.operation({'operation': 'begin', 'source': self.report, 'owner_pid': os.getpid()})['session']

    def operation(self, request, expected=0, action='capture-session'):
        result, value = public(self.verify, self.root, request, action)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return value

    def invoke(self, trace='sample-37'):
        arm = self.operation({'operation': 'arm', 'source': self.session, 'sample_key': 'q-37',
                              'native_id': 37, 'trace_id': trace})
        folder = self.runtime / arm['directory']
        payload = {'model': 'synthetic-model', 'prompt': 'synthetic question', 'temperature': 0, 'max_tokens': 2}
        request = write(self.root / 'request.raw', payload)
        url = f'http://127.0.0.1:{self.server.server_port}/v1/completions'
        with urlopen(Request(url, data=Path(request['path']).read_bytes(),
                             headers={'Content-Type': 'application/json', 'X-Precision-Trace': trace}), timeout=5) as reply:
            raw = reply.read()
        response = self.root / 'response.raw'
        response.write_bytes(raw)
        return folder, {'operation': 'seal', 'source': self.session, 'trace_id': trace,
                        'http': {'request': request, 'response': reference(response)}}

    def read(self, receipt, **selection):
        return self.operation({'source': receipt, 'request_id': 'sample-37', 'output_index': 1, **selection},
                              action='capture-read')

    def test_public_http_session_second_token_and_offline_read(self):
        self.start()
        folder, seal = self.invoke()
        sealed = self.operation(seal)
        self.operation({'operation': 'end', 'source': self.session})
        self.assertFalse((self.runtime / 'active.json').exists())
        self.assertFalse((self.runtime / 'lease.json').exists())
        row = self.read(sealed['receipt'])
        self.assertEqual(row['prefix_ids'], [11, 22, 33, 71])
        self.assertEqual((row['input_ids'], row['positions'], row['token_id'], row['candidate']), ([71], [3], 92, 92))
        self.assertEqual(row['external_id'], 'actual-sample-37')
        self.assertEqual(row['internal_id'], 'actual-sample-37-random-suffix')
        self.assertEqual(len(row['raw']['values']), 100)
        self.assertEqual(max(range(100), key=row['raw']['values'].__getitem__), 3)
        self.assertEqual(max(range(100), key=row['scores']['values'].__getitem__), 92)
        self.assertEqual(row['coverage']['module_calls'], 5)
        self.assertEqual(row['coverage']['calls'], 9)
        selected = self.read(sealed['receipt'], layer='model.layers.1', api='Torch.add', occurrence=1)
        self.assertEqual([call['call'] for call in selected['calls']],
                         ['Module.model.layers.1.Block.forward.0', 'Torch.add.11.forward'])
        for call in selected['calls']:
            for side in ('input_args', 'output'):
                self.assertTrue(Path(call['tensors'][side][0]['path']).is_file())
        self.assertEqual(len(self.read(sealed['receipt'], layer='lm_head')['calls']), 1)
        self.assertEqual(len(self.calls), 1, 'session 控制操作不能额外发送模型请求')
        self.server.shutdown()
        self.server.server_close()
        self.assertEqual(self.read(sealed['receipt'], layer='model.norm')['forward_id'], row['forward_id'])

    def test_missing_call_blocks_seal_and_preserves_lease(self):
        self.start()
        folder, seal = self.invoke()
        frame = json.loads((folder / 'prediction-1.json').read_text())
        metadata = self.runtime / frame['dump'] / 'dump.json'
        data = json.loads(metadata.read_text())
        del data['data']['Torch.add.11.forward']
        write(metadata, data)
        self.operation(seal, expected=1)
        self.assertTrue((self.runtime / 'active.json').exists())
        self.operation({'operation': 'end', 'source': self.session})
        self.assertFalse((self.runtime / 'lease.json').exists())

    def test_changed_http_and_tensor_evidence_are_rejected(self):
        self.start()
        folder, seal = self.invoke()
        original = Path(seal['http']['response']['path']).read_bytes()
        Path(seal['http']['response']['path']).write_text('{}')
        self.operation(seal, expected=1)
        Path(seal['http']['response']['path']).write_bytes(original)
        sealed = self.operation(seal)
        self.operation({'operation': 'end', 'source': self.session})
        row = self.read(sealed['receipt'])
        Path(row['calls'][0]['tensors']['output'][0]['path']).write_bytes(b'changed')
        self.operation({'source': sealed['receipt'], 'request_id': 'sample-37', 'output_index': 1},
                       expected=1, action='capture-read')

    def test_early_eos_seals_actual_count_and_missing_second_token_fails(self):
        self.start()
        self.tokens = [71]
        _, seal = self.invoke()
        sealed = self.operation(seal)
        self.assertEqual(sealed['token_count'], 1)
        self.operation({'operation': 'end', 'source': self.session})
        self.operation({'source': sealed['receipt'], 'request_id': 'sample-37', 'output_index': 1},
                       expected=1, action='capture-read')

    def test_preparation_config_and_deployment_binding_are_required(self):
        self.start()
        self.operation({'operation': 'end', 'source': self.session})
        (self.runtime / 'msprobe.json').write_text('{}')
        self.operation({'operation': 'begin', 'source': self.report, 'owner_pid': os.getpid()}, expected=1)
        self.assertFalse((self.runtime / 'lease.json').exists())

    def test_repeated_token_ids_keep_output_positions(self):
        self.start()
        self.tokens = [71, 71]
        _, seal = self.invoke()
        sealed = self.operation(seal)
        row = self.read(sealed['receipt'])
        self.assertEqual(row['prefix_ids'], [11, 22, 33, 71])
        self.assertEqual((row['output_index'], row['positions'], row['token_id']), (1, [3], 71))
        self.operation({'operation': 'end', 'source': self.session})

    def test_nonproducing_forward_is_sealed_and_rechecked(self):
        self.start()
        folder, seal = self.invoke()
        original = json.loads((folder / 'prediction-0.json').read_text())
        dump, calls = native.native_dump(self.runtime, 99)
        extra = {**original, 'forward_id': 'chunked-prefill', 'step': 99,
                 'dump': str(dump.relative_to(self.runtime)), 'output_index': None}
        write(folder / 'begin-chunked-prefill.json', extra)
        write(folder / 'end-chunked-prefill.json', {**extra, 'status': 'completed'})
        sealed = self.operation(seal)
        self.operation({'operation': 'end', 'source': self.session})
        receipt = json.loads(Path(sealed['receipt']['path']).read_text())
        self.assertEqual(len(receipt['extra_forwards']), 1)
        self.read(sealed['receipt'])
        (dump / 'dump_tensor_data' / (calls[0] + '-output.pt')).unlink()
        self.operation({'source': sealed['receipt'], 'request_id': 'sample-37', 'output_index': 1},
                       expected=1, action='capture-read')

    def test_end_does_not_release_inflight_request(self):
        self.start()
        folder, _ = self.invoke()
        (folder / 'frontend-end.json').unlink()
        self.operation({'operation': 'end', 'source': self.session}, expected=1)
        self.assertTrue((self.runtime / 'lease.json').exists())
        write(folder / 'frontend-end.json', {'trace_id': 'sample-37'})
        self.operation({'operation': 'end', 'source': self.session})
