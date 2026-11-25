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

    def test_audited_target_chat_method_and_source_identity(self):
        from verification.runtime.capture.adapters import frontend, state
        from verification.runtime.capture.adapters.sources import SOURCES
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from worker_capture.fixtures import audited_chat
        digest = '8c41af967ef813bf6fd2e2f16b8eedbbf6098d78c971639a58b6297d1c217c02'
        self.assertIn(digest, SOURCES['vllm.entrypoints.openai.chat_completion.serving'])
        class Sampling: pass
        received = []
        class Client:
            async def generate(self, prompt, sampling_params, request_id, **kwargs):
                received.append((request_id, kwargs))
                for finished in (False, True):
                    yield NS(request_id=request_id, prompt_token_ids=[11,22], finished=finished,
                             outputs=[NS(token_ids=[71,92], finish_reason='length')])
        class Serving(audited_chat.OpenAIServingChat):
            def __init__(self):
                self.engine_client = Client(); self.renderer = NS(tokenizer=object())
                self.reasoning_parser_cls = None
                self.models = NS(model_name=lambda _: 'model')
                self.default_sampling_params = {}
            async def render_chat_request(self, request): return [], ['engine-input']
            async def _get_trace_headers(self, headers): return None
            async def chat_completion_full_generator(self, request, generator, request_id, *args):
                async for _ in generator: pass
                return NS(id=request_id)
            _effective_chat_template_kwargs = lambda self, request: {}
            _base_request_id = lambda self, raw, request_id: request_id
            _maybe_get_adapters = lambda self, request, **kwargs: None
            _get_data_parallel_rank = lambda self, raw: 0
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, VERIFY_EAGER_ROOT=tmp), patch.object(state, 'ready'):
            root=Path(tmp); folder=root/'request'; folder.mkdir()
            state.write(root/'active.json', {'directory':'request'})
            state.write(folder/'intent.json', {'session_mode':True,'trace_id':'trace'})
            with patch.object(audited_chat, 'RequestResponseMetadata', NS, create=True):
                frontend.install(Serving, 'create_chat_completion')
                request=NS(request_id='actual-client-id', max_completion_tokens=2, max_tokens=None,
                           use_beam_search=False, include_reasoning=False, stream=False, priority=3,
                           to_sampling_params=lambda *args: Sampling())
                raw=NS(headers={'X-Precision-Trace':'trace'}, state=NS())
                response=asyncio.run(Serving().create_chat_completion(request, raw_request=raw))
            self.assertEqual(received[0][0], 'chatcmpl-actual-client-id')
            self.assertEqual(received[0][1]['priority'], 3)
            self.assertIs(received[0][1]['reasoning_ended'], True)
            self.assertEqual(state.read(folder/'frontend.json')['response_id'], response.id)
            self.assertEqual(state.read(folder/'engine-output.json')['token_ids'], [71,92])
            self.assertTrue((folder/'frontend-end.json').is_file())

    def test_unknown_source_is_rejected_without_installing_hook(self):
        from verification.runtime.capture.adapters import install, frontend, state
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, VERIFY_EAGER_ROOT=tmp):
            path=Path(tmp)/'changed.py'; path.write_text('# unreviewed source\n')
            module=NS(__name__='vllm.entrypoints.openai.chat_completion.serving', __file__=str(path))
            with patch.object(frontend, 'install') as attach:
                install.attach(module)
            attach.assert_not_called()
            errors=list(Path(tmp).glob('error-*.json'))
            self.assertEqual(len(errors), 1)
            self.assertIn('未适配的目标源码', state.read(errors[0])['error'])
