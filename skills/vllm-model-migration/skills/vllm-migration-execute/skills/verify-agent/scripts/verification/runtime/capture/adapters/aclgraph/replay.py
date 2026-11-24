"""按真实 NPUGraph 对象绑定捕图身份，按请求前向记录成功的 replay。"""
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4
from weakref import WeakKeyDictionary

active = ContextVar('aclgraph_forward', default=None)
capturing = ContextVar('aclgraph_capture', default=None)


class ReplayLog:
    def __init__(self, mode):
        self.mode = mode
        self.graphs = WeakKeyDictionary()
        self.events = None
        self.expected = {}
        self.calibrating = None

    @contextmanager
    def calibration(self):
        previous = self.calibrating
        self.calibrating = []
        try:
            yield self.calibrating
            sequence = tuple(self.calibrating)
            for graph_id in sequence:
                self.expected[graph_id] = sequence
        finally:
            self.calibrating = previous

    def capture(self, graph, wrapper, descriptor, mode, **context):
        self.graphs[graph] = {'graph_id': uuid4().hex, 'wrapper_id': wrapper, 'descriptor': descriptor,
                              'mode': mode, 'ordinal': 0, **context}
        if self.calibrating is not None:
            self.calibrating.append(self.graphs[graph]['graph_id'])
        return self.graphs[graph]

    def start(self):
        self.events = []
        self.errors = []

    def replayed(self, graph, *, record=True):
        entry = self.graphs.get(graph)
        if entry is None:
            if record and self.events is not None:
                self.errors.append('重放的图对象没有捕图记录')
            return
        entry['ordinal'] += 1
        if record and self.events is not None:
            self.events.append({key: entry[key] for key in
                                ('graph_id', 'wrapper_id', 'descriptor', 'mode', 'ordinal')})

    def finish(self):
        events, self.events = self.events, None
        if not events or any(e['mode'] != self.mode for e in events):
            self.errors.append('目标前向缺少申请模式的真实图重放')
        if self.mode == 'FULL' and len(events) != 1:
            self.errors.append('FULL 前向需要一次整图重放')
        if events and self.mode == 'PIECEWISE':
            actual = tuple(e['graph_id'] for e in events)
            if self.expected.get(actual[0]) != actual:
                self.errors.append('分段图序列与捕图时实际子图不符')
        return {'replays': events, 'errors': list(self.errors)}
