"""有 msProbe 的 CPU 环境执行真实模型、DataCollector 和本仓目标采集器。"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


class NativeProbeTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('msprobe'), '当前解释器未加载可选 msProbe')
    def test_real_collector_precedes_tensor_copy_and_preserves_next_invocation(self):
        env = os.environ.copy()
        verify = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
        env['PYTHONPATH'] = os.pathsep.join([str(verify), *sys.path])
        worker = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), 'native'],
                                env=env, text=True, capture_output=True)
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)


def real_probe():
    import json
    from tempfile import TemporaryDirectory
    import torch
    from msprobe.pytorch import PrecisionDebugger
    from verification.runtime.capture.adapters.alignment.gate import TargetGate
    from verification.runtime.capture.adapters.alignment.worker import check_observer
    from verification.runtime.capture.contract import native_config
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        cfg = native_config(root/'native')
        cfg['dump_enable'] = True
        (root/'cfg.json').write_text(json.dumps(cfg))
        model = torch.nn.Sequential(torch.nn.Linear(4,4), torch.nn.Linear(4,4)).eval()
        probe = PrecisionDebugger(str(root/'cfg.json'))
        with unittest.TestCase().assertRaisesRegex(ValueError, '独占'):
            check_observer(None)
        check_observer(probe)
        call = {'id':'linear','kind':'api','occurrence':0,'npu':{'owner':'1','name':'Functional.linear'},
                'slots':[{'id':'w','npu':['input_args',1]}, {'id':'y','npu':['output',0]}]}
        gate = TargetGate({'calls':[call]},root)
        x = torch.ones(1,4)
        with gate.intercept(probe.service.data_collector):
            probe.start(model=model,rank_id=0)
            with unittest.TestCase().assertRaisesRegex(ValueError, '独占'):
                check_observer(probe)
            with torch.no_grad():
                result = model(x)
            probe.stop()
            probe.step()
        assert not gate.errors, gate.errors
        assert [(r['slot'],r['native_parent']) for r in gate.rows] == [('w', '1'), ('y','1')], gate.rows
        assert {r['path'] for r in gate.rows} == {str(p.relative_to(root)) for p in root.rglob('*.pt')}
        for row, expected in zip(gate.rows, (model[1].weight,result), strict=True):
            torch.testing.assert_close(torch.load(root/row['path'],map_location='cpu',weights_only=True),expected)
        # Ordinary collection through the same debugger must still capture all covered calls.
        probe.start(model=model,rank_id=0)
        with torch.no_grad():
            model(x)
        probe.stop()
        probe.step()
        assert len(list((root/'native/step1').rglob('*.pt'))) > 2

        # Module weights are not necessarily explicit API operands, and share an invocation with outputs.
        dest = root / 'module'
        dest.mkdir()
        module_call = {'id':'layer','kind':'module','occurrence':0,'npu':{'owner':'','name':'1'},
                       'slots':[{'id':'w','role':'weight','npu':['parameters','weight']},
                                {'id':'y','role':'output','npu':['output',0]}]}
        module_gate = TargetGate({'calls':[module_call]},dest)
        with module_gate.intercept(probe.service.data_collector):
            probe.start(model=model,rank_id=0)
            with torch.no_grad():
                y = model(x)
            probe.stop()
            probe.step()
        assert not module_gate.errors, module_gate.errors
        assert len(module_gate.rows) == 2 and not list((root/'native/step2').rglob('*.pt'))
        for row, value in zip(module_gate.rows, (model[1].weight,y), strict=True):
            assert row['native_name'].startswith('Module.1.Linear.forward.')
            torch.testing.assert_close(torch.load(dest/row['path'],weights_only=True), value)



if __name__ == '__main__' and sys.argv[1:] == ['native']:
    real_probe()
