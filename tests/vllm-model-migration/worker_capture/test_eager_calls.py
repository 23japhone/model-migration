"""编译装饰器的直接 forward 路径仍须产生模块输入输出，不能豁免覆盖。"""
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

VERIFY = next((Path(__file__).resolve().parents[3]/'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))


class Module:
    def __init__(self):
        self._forward_pre_hooks = {}; self._forward_hooks = {}
        self._backward_pre_hooks = {}; self._backward_hooks = {}
        self.calls = 0
    def forward(self, value, *, extra):
        self.calls += 1
        return value + extra
    def _call_impl(self, *args, **kwargs):
        for hook in self._forward_pre_hooks.values(): hook(self, args, kwargs)
        result = self.forward(*args, **kwargs)
        for hook in self._forward_hooks.values(): hook(self, args, kwargs, result)
        return result


class EagerCallsTests(unittest.TestCase):
    def fixture(self):
        from verification.runtime.capture.adapters import eager_calls
        class Compiled(Module):
            do_not_compile = True
            # 与审核目标 do_not_compile 分支一致，绕过 Module._call_impl。
            def __call__(self, *args, **kwargs): return self.forward(*args, **kwargs)
        Compiled.__call__.__module__ = 'vllm.compilation.decorators'
        model = Compiled(); recorder = NS(frame=None)
        root = NS(named_modules=lambda: iter([('language_model.model', model)]))
        digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.enterContext(patch.dict(eager_calls.SOURCES, {'vllm.compilation.decorators': (digest,)}))
        globals_ = NS(**{name: {} for name in eager_calls.GLOBAL_HOOKS})
        self.enterContext(patch.dict(sys.modules, {'torch.nn': NS(Module=Module),
            'torch.nn.modules.module': globals_}))
        return eager_calls, model, root, recorder

    def test_active_instance_uses_hooks_once_and_preserves_other_calls(self):
        helper, model, root, recorder = self.fixture()
        bridge = helper.EagerCalls(root, recorder); bridge.validate()
        events = []
        model._forward_pre_hooks[1] = lambda module, args, kw: events.append(('input', args, kw))
        model._forward_hooks[2] = lambda module, args, kw, result: events.append(('output', result))
        self.assertEqual(model(3, extra=4), 7); self.assertEqual(events, [])
        recorder.frame = {'forward_id': 'actual'}
        self.assertEqual(model(5, extra=6), 11)
        self.assertEqual(events, [('input', (5,), {'extra': 6}), ('output', 11)])
        other = type(model)(); other._forward_pre_hooks[1] = lambda *args: self.fail('other instance changed')
        self.assertEqual(other(1, extra=2), 3)
        recorder.frame = None
        self.assertEqual(model(7, extra=8), 15); self.assertEqual(model.calls, 3)
        self.assertEqual(bridge.paths, ['language_model.model'])

    def test_existing_hooks_or_changed_execution_mode_are_rejected(self):
        helper, model, root, recorder = self.fixture()
        model._forward_hooks[1] = object()
        with self.assertRaisesRegex(ValueError, '已有.*hook'): helper.EagerCalls(root, recorder)
        model._forward_hooks.clear()
        bridge = helper.EagerCalls(root, recorder)
        model._forward_pre_hooks[2] = object()
        with self.assertRaisesRegex(ValueError, '已有.*hook'): bridge.validate()
        model._forward_pre_hooks.clear(); model.do_not_compile = False
        recorder.frame = {}
        with self.assertRaisesRegex(ValueError, 'Eager'): model(1, extra=2)

    def test_unknown_decorator_and_global_hooks_are_rejected(self):
        helper, model, root, recorder = self.fixture()
        with patch.dict(helper.SOURCES, {'vllm.compilation.decorators': ('0'*64,)}):
            with self.assertRaisesRegex(ValueError, '源码'): helper.EagerCalls(root, recorder)
        sys.modules['torch.nn.modules.module']._global_forward_hooks[1] = object()
        with self.assertRaisesRegex(ValueError, '全局.*hook'): helper.EagerCalls(root, recorder)
