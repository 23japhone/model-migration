"""在正式前端上下文中观察 engine_client.generate 的真实 ID 和最终输出。"""
from contextvars import ContextVar
from functools import wraps
import inspect
import hashlib
import json
from time import monotonic

from . import state
from .batch import members
from .streaming import engine_event, traced_stream

CURRENT = ContextVar('verify_precision_request', default=None)


def wrap_client(client):
    if getattr(client, '_verify_precision_wrapped', False): return
    original = client.generate
    signature = inspect.signature(original)
    @wraps(original)
    async def generate(*args, **kwargs):
        target = CURRENT.get()
        if target:
            folder, trace = target
            bound = signature.bind(*args, **kwargs)
            external = bound.arguments['request_id']
            target = members.publish_frontend(folder, trace, external)
            params = bound.arguments.get('sampling_params')
            delta = getattr(getattr(params, 'output_kind', None), 'name', '') == 'DELTA'
            tokens, prompt = [], []
        async for result in original(*args, **kwargs):
            if target:
                try:
                    engine_event(folder, result, external, tokens, delta, prompt)
                except Exception as error: state.fail(folder, error)
            yield result
    client.generate = generate
    client._verify_precision_wrapped = True


def install(cls, method):
    initialize, original = cls.__init__, getattr(cls, method)
    signature = inspect.signature(original)
    @wraps(initialize)
    def init(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        wrap_client(self.engine_client)
        state.ready('frontend', generation_contract=1, hooks=[method, 'engine_client.generate'])
    @wraps(original)
    async def invoked(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        raw = bound.arguments.get('raw_request')
        trace = raw.headers.get('X-Precision-Trace') if raw is not None else None
        folder = members.frontend_folder(trace) if trace else None
        if not trace or not folder: return await original(self, *args, **kwargs)
        intent = state.read(folder/'intent.json')
        if not intent.get('session_mode') or trace != intent.get('trace_id'):
            state.fail(folder, '前端 trace 与采集租约不一致')
            return await original(self, *args, **kwargs)
        token = CURRENT.set((folder, trace))
        started = monotonic()
        streaming = False
        try:
            if hasattr(raw, 'json'):
                payload = await raw.json()
                canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                state.write(folder/'frontend-payload-digest.json', {'sha256': hashlib.sha256(canonical).hexdigest(),
                            'http_sha256': hashlib.sha256(await raw.body()).hexdigest()})
            response = await original(self, *args, **kwargs)
            if hasattr(response, '__aiter__'):
                streaming = True
                return traced_stream(response, (folder, trace), CURRENT, started)
            if (folder/'frontend.json').exists():
                mapped = state.read(folder/'frontend.json')
                state.write(folder/'frontend.json', {**mapped, 'response_id': response.id})
            return response
        except Exception as error:
            state.fail(folder, error)
            raise
        finally:
            if not streaming:
                state.write(folder/'frontend-end.json', {'trace_id': trace, 'request_wall_seconds': monotonic() - started})
            CURRENT.reset(token)
    cls.__init__ = init
    setattr(cls, method, invoked)
