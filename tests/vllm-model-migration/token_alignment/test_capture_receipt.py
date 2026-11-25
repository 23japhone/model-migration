"""走真实会话 CLI 验证单题向量来源、HTTP 原件和关闭采集后的独立回读。"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS))
from worker_capture.session_fixture import deployment, isolated_bundle, public, reference, write


class TokenReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cli = isolated_bundle(self.root)
        source, self.runtime = deployment(self.root, 'http://127.0.0.1:8888/v1')
        worker = self.runtime / 'worker.json'
        write(worker, {**json.loads(worker.read_text()), 'mode': 'eager', 'token_prediction': True, 'vocab_size': 3})
        begun = self.success({'operation': 'begin', 'source': source, 'owner_pid': os.getpid()})
        self.session = begun['session']
        trace = 'question-73'
        armed = self.success({'operation': 'arm', 'source': self.session, 'trace_id': trace,
                              'sample_key': 73, 'native_id': 'native-73', 'profile': 'token_prediction'})
        self.folder = self.runtime / armed['directory']
        prompt = [1, 2]
        write(self.folder / 'request.json', {'internal_id': 'req-1', 'external_id': 'ext-1', 'prompt_ids': prompt})
        write(self.folder / 'frontend.json', {'trace_id': trace, 'external_id': 'ext-1', 'response_id': 'completion'})
        write(self.folder / 'frontend-end.json', {'trace_id': trace})
        write(self.folder / 'engine-output.json', {'external_id': 'ext-1', 'prompt_ids': prompt, 'finished': True,
                                                 'token_ids': [2], 'finish_reason': 'stop'})
        frame = {'forward_id': 'abc', 'internal_id': 'req-1', 'output_index': 0, 'profile': 'token_prediction',
                 'input_ids': prompt, 'positions': [0, 1], 'logits_indices': [1], 'candidate': 2}
        for name, values in [('raw', (5., 2., 0.)), ('processed_probs', (.125, .125, .75))]:
            path = self.folder / f'abc-{name}.bin'
            path.write_bytes(struct.pack('<3f', *values))
            frame[name] = {'file': path.name, 'size': 3, 'dtype': 'torch.float32'}
        for name in ('abc', 'prediction-0', 'begin-abc', 'end-abc'):
            write(self.folder / f'{name}.json', {**frame, **({'status': 'completed'} if name == 'end-abc' else {})})
        write(self.folder / 'accepted-0.json', {'internal_id': 'req-1', 'forward_id': 'abc', 'output_index': 0,
                                                 'token_id': 2, 'finish_reason': 'stop'})
        self.payload = {'model': 'synthetic-model', 'temperature': 0., 'return_token_ids': True, 'messages': []}
        request = write(self.root / 'http-request', self.payload)
        write(self.folder / 'frontend-payload-digest.json', {'http_sha256': request['sha256']})
        response = write(self.root / 'http-response', {'id': 'completion', 'choices': [
            {'index': 0, 'token_ids': [2], 'finish_reason': 'stop', 'stop_reason': 2}]})
        self.seal = {'operation': 'seal', 'source': self.session, 'trace_id': trace,
                     'http': {'request': request, 'response': response}}

    def success(self, request):
        process, report = public(self.cli, self.root, request)
        self.assertEqual(process.returncode, 0, (report, process.stderr))
        return report

    def test_token_sample_portable_raw_and_normalized_distribution(self):
        sealed = self.success(self.seal)
        ref = sealed['token_sample']
        path = Path(ref['path'])
        self.assertEqual(reference(path), ref)
        record = json.loads(path.read_text())
        self.assertEqual(record['sample_id'], 73)
        self.assertEqual((record['evidence_kind'], record['stop_reason']), ('runtime', 'stop_token:2'))
        receipt = json.loads(Path(sealed['receipt']['path']).read_bytes())
        self.assertEqual((receipt['finish_reason'], receipt['stop_trigger']), ('stop', 2))
        self.assertEqual((record['prompt_token_ids'], record['output_token_ids']), ([1, 2], [2]))
        for name, values in [('raw_logits', (5., 2., 0.)), ('processed_probs', (.125, .125, .75))]:
            row, = record[name]
            self.assertFalse(Path(row['path']).is_absolute())
            data = (path.parent / row['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
            self.assertEqual((row['dtype'], row['size'], struct.unpack('<3f', data)), ('float32', 3, values))
        self.assertFalse((self.runtime / 'dump/native').exists())
        self.success({'operation': 'end', 'source': self.session})

    def test_server_did_not_receive_substituted_http_body(self):
        self.seal['http']['request'] = write(self.root / 'other', {**self.payload, 'messages': ['different']})
        process, value = public(self.cli, self.root, self.seal)
        self.assertNotEqual(process.returncode, 0, value)
        self.assertFalse((self.folder / 'token-sample.json').exists())

    def test_different_http_token_fails_even_with_same_finish_reason(self):
        self.seal['http']['response'] = write(self.root / 'http-response', {'id': 'completion', 'choices': [
            {'index': 0, 'token_ids': [1], 'finish_reason': 'stop'}]})
        process, value = public(self.cli, self.root, self.seal)
        self.assertNotEqual(process.returncode, 0, value)
        self.assertFalse((self.folder / 'token-sample.json').exists())
