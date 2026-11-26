"""每个物理前向只打开一次原生窗口，compute_logits 完成后关闭。"""

from contextlib import contextmanager, ExitStack
from inspect import signature

from .. import state
from ..decode_cache.snapshots import measure_observation, observer_only


# These decoder blocks consume packed hidden states and return packed hidden states and residuals.
PACKED_BLOCKS = {
    'vllm.model_executor.models.llama.LlamaDecoderLayer',
    'vllm.model_executor.models.qwen3_5.Qwen3_5DecoderLayer',
    'vllm.model_executor.models.deepseek_v2.DeepseekV2DecoderLayer',
}


@contextmanager
def model(rec, plan, rows):
    if not rows:
        yield
        return
    frame = {
        'forward_id': plan['forward_id'],
        'group_id': plan['group_id'],
        'profile': 'batch_joint',
        'errors': [],
        'rows': rows,
        'positions': [p for r in rows for p in r['positions']],
    }
    rec.frame, rec.folder = frame, state.root() / plan['group_id']
    try:
        modules = dict(rec.runner.get_model().named_modules())
        frame['signatures'] = {
            r['path']: list(signature(type(modules[r['path']]).forward).parameters)[1:]
            for r in rec.architecture['layers'] if r['class'] in PACKED_BLOCKS
        }
        rec.start_probe()
    except Exception as error:
        frame['errors'].append('msProbe start: ' + str(error))
    with ExitStack() as stack:
        if 'cache_capture' in plan:
            try:
                from .layers import install

                stack.enter_context(measure_observation(frame))
                with observer_only():
                    install(stack, rec, plan, rows, frame)
            except Exception as error:
                frame['errors'].append('mindie: ' + str(error))
        yield


def finish(rec, plan):
    frame = rec.frame
    if not frame or frame.get('profile') != 'batch_joint':
        return
    try:
        pending = rec.runner.execute_model_state
        if pending is None or pending.logits is None:
            raise ValueError('实际 execute_model_state 缺少 logits')
        save_logits(rec, frame, pending.logits)
    except Exception as error:
        frame['errors'].append('logits: ' + str(error))
    try:
        rec.close()
    except Exception as error:
        frame['errors'].append('msProbe stop: ' + str(error))
    finally:
        rec.frame = None
    state.write(rec.folder / ('numeric-' + plan['forward_id'] + '.json'), frame)


def save_logits(rec, frame, logits):
    from ..worker import save_vector

    rows = frame['rows']
    if list(rec.runner.input_batch.req_ids) != [r['internal_id'] for r in rows] or logits.shape != (
        len(rows),
        rec.size,
    ):
        raise ValueError('logits 批行与 worker 不符')
    with observer_only():
        for row in rows:
            i = row['batch_row']
            path = rec.folder / ('member-' + row['trace_id']) / (frame['forward_id'] + '-logits.bin')
            row['logits'] = save_vector(path, logits[i : i + 1], rec.size)
