"""独立记录实际模块调用，并核对 msProbe 每次收集的进入与返回。"""
from collections import Counter
from functools import wraps
import re


def check_support():
    from inspect import signature
    from msprobe.core.dump.data_dump.data_collector import DataCollector
    required = {'self', 'name', 'module', 'pid', 'module_input_output'}
    for name in ('forward_input_data_collect', 'forward_output_data_collect'):
        if set(signature(getattr(DataCollector, name)).parameters) != required:
            raise ValueError('此 msProbe 版本缺少已适配的完整调用观察接点')


def normalized(name):
    return re.sub(r'\.(\d+)\.forward$', r'.forward.\1', name)


class Coverage:
    def __init__(self, recorder, model):
        check_support()
        self.recorder = recorder
        self.events = None
        self.modules = None
        for name, module in model.named_modules():
            if name: self.module(name, module)

    def module(self, name, module):
        original = module.forward
        @wraps(original)
        def invoked(*args, **kwargs):
            if self.modules is not None: self.modules['begin'].append(name)
            result = original(*args, **kwargs)
            if self.modules is not None: self.modules['end'].append(name)
            return result
        module.forward = invoked

    def attach(self, debugger):
        collector = debugger.service.data_collector
        for side, method in (('input', 'forward_input_data_collect'), ('output', 'forward_output_data_collect')):
            self.collector(collector, side, method)

    def collector(self, collector, side, method):
        original = getattr(collector, method)
        @wraps(original)
        def observed(name, *args, **kwargs):
            if self.events is not None: self.events[side].append(normalized(name))
            return original(name, *args, **kwargs)
        setattr(collector, method, observed)

    def start(self):
        self.events = {'input': [], 'output': []}
        self.modules = {'begin': [], 'end': []}

    def finish(self):
        value = {'calls': self.events, 'modules': self.modules,
                 'scope': 'all_invoked_modules_and_msprobe_registered_apis',
                 'device_kernels': 'not_observed'}
        self.events = self.modules = None
        return value


def verify(coverage, calls):
    events = coverage['calls']; modules = coverage['modules']
    if Counter(events['input']) != Counter(events['output']): raise ValueError('算子/模块进入与返回不配对')
    if Counter(modules['begin']) != Counter(modules['end']): raise ValueError('实际模块前向未完成')
    indexed = {normalized(row['call']): row for row in calls}
    expected = {normalized(name) for name in events['input']}
    if expected != set(indexed):
        raise ValueError('msProbe 调用清单漏写或额外调用：' + str(sorted(expected ^ set(indexed))))
    observed = Counter(row['module'] for row in calls if row['module'])
    if observed != Counter(modules['end']): raise ValueError('实际模块调用与 dump 模块清单不一致')
    for row in calls:
        data = row['tensors']; values = row['values']
        if not (data['input_args'] or data['input_kwargs'] or values) or not (data['output'] or values):
            raise ValueError('调用输入输出缺失：' + row['call'])
    return {'status': 'complete_within_scope', 'scope': coverage['scope'],
            'module_calls': sum(observed.values()), 'calls': len(calls), 'device_kernels': 'not_observed'}
