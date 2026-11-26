"""对已经安装 Eager 观察的 worker 增加跨执行、采样调用的对象绑定。"""
from functools import wraps

from .. import state
from .worker import Pending, track_output


def install(cls):
    execute, sample = cls.execute_model, cls.sample_tokens

    @wraps(execute)
    def executed(self, schedule, *args, **kwargs):
        result = execute(self, schedule, *args, **kwargs)
        plan = getattr(schedule, '_verify_batch', None)
        if plan and plan.get('asynchronous') and not plan['foreign_members']:
            try:
                if not hasattr(self, '_verify_pending_states'):
                    self._verify_pending_states = Pending()
                self._verify_pending_states.bind(self.execute_model_state, plan)
            except Exception as error:
                state.fail(state.root() / plan['group_id'], error)
        return result

    @wraps(sample)
    def sampled(self, *args, **kwargs):
        bindings = getattr(self, '_verify_pending_states', None)
        plan = bindings.take(self.execute_model_state) if bindings else None
        result = sample(self, *args, **kwargs)
        if plan:
            try:
                track_output(result, plan)
            except Exception as error:
                state.fail(state.root() / plan['group_id'], error)
        return result

    cls.execute_model, cls.sample_tokens = executed, sampled
