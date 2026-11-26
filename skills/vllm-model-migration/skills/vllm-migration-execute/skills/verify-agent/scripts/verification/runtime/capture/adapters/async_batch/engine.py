"""在引擎实际提交点观察已有 Future，调度行为与取结果时点由引擎控制。"""
from functools import wraps
from time import monotonic_ns

from .. import state
from . import sources


def identity(plan):
    return tuple(plan[k] for k in ('group_id', 'schedule_id', 'forward_id'))


def outstanding(queue, plan):
    pending = []
    for future, schedule, _ in queue:
        prev = getattr(schedule, '_verify_batch', {})
        if prev.get('group_id') == plan['group_id'] and not future.done():
            pending.append(prev['forward_id'])
    return pending


def check_output(plan, result):
    if getattr(result, '_verify_origin', None) != identity(plan):
        raise ValueError('采样返回对象与提交的异步前向不符')


def setup(core):
    executor = core.model_executor
    scheduler = sources.verify(type(core.scheduler))
    backend = sources.verify(type(executor))
    if not core.async_scheduling or core.batch_queue is None or core.batch_queue_size < 2:
        raise ValueError('实际异步批次队列未启用')
    original = executor.execute_model

    @wraps(original)
    def execute(schedule, *args, **kwargs):
        plan = getattr(schedule, '_verify_batch', None)
        if not plan or not plan.get('asynchronous'):
            return original(schedule, *args, **kwargs)
        group = state.root() / plan['group_id']
        entered = monotonic_ns()
        try:
            pending = outstanding(core.batch_queue, plan)
        except Exception as error:
            state.fail(group, error)
            pending = []
        try:
            result = original(schedule, *args, **kwargs)
        except Exception:
            (group / ('inflight-' + plan['forward_id'] + '.json')).unlink(missing_ok=True)
            raise
        submitted = monotonic_ns()
        try:
            state.write(group / ('submit-' + plan['forward_id'] + '.json'), {
                'origin': identity(plan), 'entered_ns': entered, 'submitted_ns': submitted,
                'previous_pending': pending, 'queue_depth': core.batch_queue_size,
            })
        except OSError as error:
            state.fail(group, error)
        return result

    executor.execute_model = execute
    from .terminal import observe
    observe(core)
    state.ready('async-engine', queue_depth=core.batch_queue_size, scheduler=scheduler, executor=backend)


def install(cls):
    original = cls.__init__

    @wraps(original)
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        try:
            setup(self)
        except Exception as error:
            state.fail(state.root(), error)

    cls.__init__ = initialize
