"""给实际调度结果携带组内身份；接受结果绑定本次 update_from_output 的参数。"""
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from .. import state
from ..decode_cache.plan import step_position
from . import members
from ..async_batch import retire

accepting = ContextVar('verify_accept_batch', default=None)


def dispatch(group, schedule, ordinal, scheduler=None):
    planned = {}
    for internal in schedule.num_scheduled_tokens:
        trace = members.resolve(group, 'internal', internal)
        if trace:
            planned[internal] = {'trace_id': trace, **step_position(schedule, internal, members.member(group, trace))}
    if not planned:
        return
    plan = {'group_id': group.name, 'ordinal': ordinal, 'forward_id': uuid4().hex,
            'schedule_id': uuid4().hex, 'requests': planned,
            'foreign_members': len(planned) != len(schedule.num_scheduled_tokens)}
    config = state.read(group / 'group.json')
    if 'async_queue' in config:
        plan['asynchronous'] = True
    if config.get('profile') == 'batch_joint':
        plan['profile'] = 'batch_joint'
        if 'cache_capture' in config and not plan['foreign_members']:
            from ..decode_cache.plan import allocation
            plan['cache_capture'] = config['cache_capture']
            for internal, step in planned.items():
                step['groups'] = allocation(scheduler, internal, step['output_index'], step['position'])['groups']
    state.write(group / f'dispatch-{ordinal}.json', plan)
    if plan.get('asynchronous'):
        state.write(group / ('inflight-' + plan['forward_id'] + '.json'),
                    {'members': [s['trace_id'] for s in planned.values()]})
    schedule._verify_batch = plan


def record_samples(plan, output):
    group = state.root() / plan['group_id']
    mapping = output.req_id_to_index
    ordered = output.req_ids
    expected = {internal: i for i, internal in enumerate(ordered)}
    if (set(mapping) != set(plan['requests']) or mapping != expected or len(expected) != len(ordered)
            or len(output.sampled_token_ids) != len(ordered)):
        raise ValueError('采样结果行与本次调度成员不符')
    rows = {key: {'sample_row': row, 'token_ids': list(output.sampled_token_ids[row])}
            for key, row in mapping.items()}
    state.write(group / ('samples-' + plan['forward_id'] + '.json'), rows)


def accepted(plan, request, result):
    if not result[0] or request.request_id not in plan['requests']:
        return
    group = state.root() / plan['group_id']
    trace = plan['requests'][request.request_id]['trace_id']
    folder = members.member(group, trace)
    try:
        token, = result[0]
        index = request.num_output_tokens - 1
        path = folder / f'accepted-{index}.json'
        if path.exists():
            raise ValueError('同一输出序号被重复接受')
        if plan.get('asynchronous'):
            plan.setdefault('_accepted', {})[request.request_id] = index
        state.write(path, {'internal_id': request.request_id, 'output_index': index,
                           'forward_id': plan['forward_id'], 'token_id': token})
    except Exception as error:
        state.fail(folder, error)


def install(cls):
    if not hasattr(cls, 'update_from_output') or not hasattr(cls, 'schedule'):
        return
    schedule, update = cls.schedule, cls.update_from_output

    @wraps(schedule)
    def scheduled(self, *args, **kwargs):
        output = schedule(self, *args, **kwargs)
        group = members.active()
        if group:
            try:
                ordinal = getattr(self, '_verify_batch_ordinal', 0)
                dispatch(group, output, ordinal, self)
                self._verify_batch_ordinal = ordinal + 1
            except Exception as error:
                state.fail(group, error)
        return output

    @wraps(update)
    def updated(self, scheduler_output, model_runner_output, *args, **kwargs):
        plan = getattr(scheduler_output, '_verify_batch', None)
        context = accepting.set(plan)
        asynchronous = plan and plan.get('asynchronous')
        dispositions = {}
        failed = True
        try:
            if plan and not plan['foreign_members']:
                try:
                    if asynchronous:
                        dispositions = retire.before(self, plan, model_runner_output)
                    record_samples(plan, model_runner_output)
                except Exception as error:
                    group = state.root() / plan['group_id']
                    state.write(group / ('samples-' + plan['forward_id'] + '.json'), {'error': str(error)})
            result = update(self, scheduler_output, model_runner_output, *args, **kwargs)
            failed = False
            return result
        finally:
            accepting.reset(context)
            if asynchronous:
                try:
                    retire.complete(plan, dispositions, failed)
                except OSError as error:
                    state.fail(state.root() / plan['group_id'], error)

    cls.schedule, cls.update_from_output = scheduled, updated
