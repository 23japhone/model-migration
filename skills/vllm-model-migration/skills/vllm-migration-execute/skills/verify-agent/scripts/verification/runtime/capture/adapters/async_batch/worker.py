"""采样调用消费确切的 execute_model_state，再将身份交给其实际异步结果。"""
from time import monotonic_ns
from .. import state
from .engine import identity


class Pending:
    def __init__(self):
        self.states = {}

    def bind(self, pending, plan):
        if pending is None or id(pending) in self.states:
            raise ValueError('异步执行状态缺失或重复')
        self.states[id(pending)] = pending, plan

    def take(self, pending):
        entry = self.states.pop(id(pending), None)
        if entry is None:
            return None
        actual, plan = entry
        if pending is not actual:
            raise ValueError('异步采样使用了其他前向的状态')
        return plan


def track_output(wrapper, plan):
    original = wrapper.get_output
    group = state.root() / plan['group_id']

    def ready():
        result = original()
        ready_ns = monotonic_ns()
        try:
            result._verify_origin = identity(plan)
            dest = group / ('ready-' + plan['forward_id'] + '.json')
            if dest.exists():
                raise ValueError('同一个异步输出被重复交付')
            state.write(dest, {'origin': identity(plan), 'ready_ns': ready_ns,
                               'req_ids': list(result.req_ids), 'sample_rows': dict(result.req_id_to_index),
                               'tokens': [list(row) for row in result.sampled_token_ids]})
        except (OSError, ValueError, AttributeError) as error:
            state.fail(group, error)
        return result

    wrapper.get_output = ready
