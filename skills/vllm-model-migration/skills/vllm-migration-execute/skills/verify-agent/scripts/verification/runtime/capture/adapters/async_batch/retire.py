"""从引擎实际消费的结果生成成员终态，接受序号仅在接受回调产生。"""
from time import monotonic_ns

from .. import state
from .engine import identity, check_output


def before(scheduler, plan, output):
    check_output(plan, output)
    dispositions = {}
    for internal, step in plan['requests'].items():
        request = scheduler.requests.get(internal)
        if request is None or request.is_finished():
            value = 'finished_or_aborted'
        elif getattr(request, 'discard_latest_async_tokens', False):
            value = 'discarded'
        elif step['position'] + step['count'] < step['prompt_length']:
            value = 'prefill_no_output'
        else:
            value = 'awaiting_accept'
        dispositions[internal] = value
    return dispositions


def complete(plan, dispositions, failed):
    group = state.root() / plan['group_id']
    for internal in dispositions:
        if internal in plan.get('_accepted', {}):
            dispositions[internal] = 'accepted'
    state.write(group / ('retired-' + plan['forward_id'] + '.json'), {
        'origin': identity(plan), 'retired_ns': monotonic_ns(), 'dispositions': dispositions,
        'indices': plan.pop('_accepted', {}), 'failed': failed,
    })
    (group / ('inflight-' + plan['forward_id'] + '.json')).unlink(missing_ok=True)


def watch_aborts(cls):
    from functools import wraps
    from ..batch import members
    original = cls.finish_requests

    @wraps(original)
    def finish(self, request_ids, finished_status):
        result = original(self, request_ids, finished_status)
        if getattr(finished_status, 'name', None) == 'FINISHED_ABORTED':
            group = members.active()
            if group:
                try:
                    for internal, _ in result:
                        trace = members.resolve(group, 'internal', internal)
                        if trace:
                            state.write(members.member(group, trace) / 'engine-abort.json',
                                        {'internal_id': internal, 'cancelled_ns': monotonic_ns()})
                except Exception as error:
                    state.fail(group, error)
        return result

    cls.finish_requests = finish
