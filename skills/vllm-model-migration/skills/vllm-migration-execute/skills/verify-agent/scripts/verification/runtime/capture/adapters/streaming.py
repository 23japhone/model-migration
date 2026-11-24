"""Keep the precision trace scoped to lazy iteration of the serving response."""
import json
from time import monotonic
from . import state


async def traced_stream(response, target, context, started):
    folder, trace = target
    iterator = response.__aiter__()
    exhausted = False
    response_id = None
    try:
        while True:
            token = context.set(target)
            try:
                chunk = await anext(iterator)
                if response_id is None:
                    text = chunk.decode() if isinstance(chunk, bytes) else chunk
                    for line in text.splitlines():
                        if line.startswith('data:') and line[5:].strip() != '[DONE]':
                            try:
                                response_id = json.loads(line[5:]).get('id')
                                if response_id:
                                    mapped = state.read(folder / 'frontend.json')
                                    state.write(folder / 'frontend.json', {**mapped, 'response_id': response_id})
                            except (ValueError, OSError, KeyError) as error:
                                state.fail(folder, error)
                            break
            except StopAsyncIteration:
                exhausted = True
                return
            finally:
                context.reset(token)
            yield chunk
    finally:
        try:
            await iterator.aclose()
        finally:
            state.write(folder / 'frontend-end.json', {'trace_id': trace, 'stream_exhausted': exhausted,
                        'request_wall_seconds': monotonic() - started})


def engine_event(folder, result, external, tokens, delta, prompt):
    if result.request_id != external or len(result.outputs) != 1:
        raise ValueError('engine output identity changed')
    item, = result.outputs
    values = list(item.token_ids)
    if delta:
        tokens.extend(values)
    else:
        if tokens != values[:len(tokens)]:
            raise ValueError('engine cumulative token prefix changed')
        tokens[:] = values
    if result.prompt_token_ids is not None:
        prompt[:] = result.prompt_token_ids
    state.write(folder / 'engine-output.json', {'external_id': external, 'prompt_ids': prompt, 'token_ids': tokens,
                'finished': result.finished, 'finish_reason': str(item.finish_reason) if result.finished else None})
