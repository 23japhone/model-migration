"""从正式前端和 generate 实参获取 ID，不推测 cmpl/chatcmpl 后缀。"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))


class FrontendTests(unittest.TestCase):
    def test_actual_request_and_response_ids(self):
        from verification.runtime.capture.adapters import frontend, engine, state
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, VERIFY_EAGER_ROOT=tmp), patch.object(state, 'ready'):
            root = Path(tmp); folder = root/'request'; folder.mkdir()
            state.write(root/'active.json', {'directory': 'request'})
            state.write(folder/'intent.json', {'session_mode': True, 'trace_id':'trace', 'request_id':'trace'})
            class Inputs:
                @staticmethod
                def assign_request_id(request):
                    request.external_req_id = request.request_id; request.request_id += '-engine-random'
            engine.input_processor(Inputs)
            class Client:
                async def generate(self, prompt, sampling_params, request_id):
                    req = NS(request_id=request_id, prompt_token_ids=[11,22])
                    Inputs.assign_request_id(req)
                    yield NS(request_id=request_id, prompt_token_ids=[11,22], finished=True,
                             outputs=[NS(token_ids=[71,92], finish_reason='length')])
            class Serving:
                def __init__(self): self.engine_client = Client()
                async def create_chat_completion(self, request, raw_request=None):
                    async for _ in self.engine_client.generate('prompt', {}, 'actual-subrequest-X'): pass
                    return NS(id='actual-response-Y')
            frontend.install(Serving, 'create_chat_completion')
            response = asyncio.run(Serving().create_chat_completion(NS(), NS(headers={'X-Precision-Trace':'trace'})))
            self.assertEqual(response.id, 'actual-response-Y')
            self.assertEqual(state.read(folder/'request.json')['internal_id'], 'actual-subrequest-X-engine-random')
            self.assertEqual(state.read(folder/'frontend.json')['response_id'], response.id)
            self.assertEqual(state.read(folder/'engine-output.json')['token_ids'], [71,92])
