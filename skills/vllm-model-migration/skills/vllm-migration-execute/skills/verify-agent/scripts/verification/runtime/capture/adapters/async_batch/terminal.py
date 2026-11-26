"""记录固定版引擎实际向前端发送 ENGINE_CORE_DEAD 的通知。"""
from functools import wraps
import os
from time import monotonic_ns

from .. import state
from ..batch import members


def observe(core):
    original = getattr(core, '_send_engine_dead', None)
    if original is None:
        return

    @wraps(original)
    def notify():
        try:
            group = members.active()
            if group:
                engine = state.read(group.parent / 'engine.json')
                if engine['pid'] == os.getpid():
                    state.write(group / 'engine-dead.json', {
                        'group_id': group.name, 'engine': {k: engine[k] for k in ('pid', 'start_ticks', 'namespace')},
                        'time_ns': monotonic_ns(), 'signal': 'ENGINE_CORE_DEAD',
                    })
        except (OSError, ValueError, KeyError):
            pass  # 诊断文件不可用时，引擎仍须通知其真实前端。
        return original()

    core._send_engine_dead = notify
