"""在正式前端上下文中观察 engine_client.generate 的真实 ID 和最终输出。"""
from contextvars import ContextVar
from functools import wraps
import inspect

from . import state

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
            if (folder/'frontend.json').exists(): raise ValueError('一道题出现多次或并行引擎请求')
            state.write(folder/'frontend.json', {'trace_id': trace, 'external_id': external})
        async for result in original(*args, **kwargs):
            if target and result.finished:
                try:
                    if len(result.outputs) != 1 or result.request_id != external:
                        raise ValueError('正式引擎输出的请求或生成数变化')
                    output, = result.outputs
                    state.write(folder/'engine-output.json', {'external_id': external,
                                'prompt_ids': list(result.prompt_token_ids), 'token_ids': list(output.token_ids),
                                'finish_reason': str(output.finish_reason)})
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
        state.ready('frontend', hooks=[method, 'engine_client.generate'])
    @wraps(original)
    async def invoked(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        raw = bound.arguments.get('raw_request')
        trace = raw.headers.get('X-Precision-Trace') if raw is not None else None
        folder = state.active()
        if not trace or not folder: return await original(self, *args, **kwargs)
        intent = state.read(folder/'intent.json')
        if not intent.get('session_mode') or trace != intent.get('trace_id'):
            state.fail(folder, '前端 trace 与采集租约不一致')
            return await original(self, *args, **kwargs)
        token = CURRENT.set((folder, trace))
        try:
            response = await original(self, *args, **kwargs)
            mapped = state.read(folder/'frontend.json')
            state.write(folder/'frontend.json', {**mapped, 'response_id': response.id})
            return response
        except Exception as error:
            state.fail(folder, error)
            raise
        finally:
            state.write(folder/'frontend-end.json', {'trace_id': trace})
            CURRENT.reset(token)
    cls.__init__ = init
    setattr(cls, method, invoked)
