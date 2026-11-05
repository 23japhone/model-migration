"""独立 ctypes 探针与宿主输出解析；模拟驱动而不分配设备。"""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))

from verification.devices import probe, inventory


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class Library:
    def __init__(self):
        self.physical = [4, 7]
        self.calls = []
        for name in probe.SIGNATURES:
            setattr(self, name, Function(lambda *args, name=name: self.invoke(name, args)))

    def invoke(self, name, args):
        self.calls.append(name)
        visible = list(map(int, os.environ[probe.RT].split(','))) if probe.RT in os.environ else [0, 1]
        values = {'aclrtGetDeviceCount': len(visible)}
        if name == 'aclrtGetLogicDevIdByUserDevId':values[name] = visible[args[0]]
        if name == 'aclrtGetPhyDevIdByLogicDevId':values[name] = self.physical[args[0]]
        if name == 'aclrtGetLogicDevIdByPhyDevId':values[name] = self.physical.index(args[0])
        if name in values:args[-1]._obj.value = values[name]
        return 0


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.library = Library()
        self.patch = patch.object(probe.ctypes, 'CDLL', return_value=self.library)
        self.patch.start(); self.addCleanup(self.patch.stop)
        library = patch.object(probe, 'library_identity', return_value={'path': '/lib', 'sha256': 'c'*64})
        library.start(); self.addCleanup(library.stop)

    def observe(self, mode, env):
        with patch.dict(os.environ, {probe.RT: '4,7'}, clear=True):
            return probe.probe({'mode': mode, 'environment': env})

    def test_bootstrap_clears_old_filter_before_query(self):
        result = self.observe('base', {probe.RT: '4,7'})
        self.assertEqual(result['status'], 'completed', result)
        self.assertNotIn(probe.RT, result['environment'])
        self.assertEqual([v['physical_id'] for v in result['devices']], [4, 7])
        self.assertEqual(self.library.calls[0], 'aclInit')
        self.assertEqual(self.library.calls[-1], 'aclFinalize')

    def test_final_probe_records_process_index_order(self):
        result = self.observe('visible', {probe.RT: '1,0'})
        self.assertEqual(result['devices'], [
            {'physical_id': 7, 'logical_id': 1, 'visible_index': 0},
            {'physical_id': 4, 'logical_id': 0, 'visible_index': 1}])
        self.assertNotIn('aclrtSetDevice', self.library.calls)

    def test_missing_symbol_blocks_before_initialization(self):
        del self.library.aclrtGetPhyDevIdByLogicDevId
        result = self.observe('base', {})
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['diagnostic']['type'], 'AttributeError')
        self.assertEqual(self.library.calls, [])

    def test_failed_call_retains_return_code_and_finalizes(self):
        self.library.aclrtGetDeviceCount = Function(lambda *args: 1234)
        result = self.observe('base', {})
        self.assertEqual(result['status'], 'blocked')
        self.assertIn({'api': 'aclrtGetDeviceCount', 'return_code': 1234}, result['calls'])
        self.assertEqual(result['calls'][-1], {'api': 'aclFinalize', 'return_code': 0})


MAP = """NPU ID    Chip ID    Chip Logic ID    Chip Name
4         0          0                Ascend 910B3
4         1          -                Mcu
7         0          1                Ascend 910B3
"""
PROCESSES = """| NPU Chip | Process id | Process name | Process memory(MB) |
+----------+------------+--------------+--------------------+
| 4 0      | 111        | python       | 1000               |
| 7 0      | 222        | python       | 1000               |
+----------+------------+--------------+--------------------+
"""

# 2026-11-03 npu-smi 25.5.1 的真实 info -m 输出；用转义字符串保留尾部空白。
MAP_910B2_CAPTURE = (
    '\tNPU ID                         Chip ID                        Chip Logic ID                  Chip Name                     \n'
    '\t0                              0                              0                              Ascend 910B2\n'
    '\t0                              1                              -                              Mcu                           \n'
    '\t1                              0                              1                              Ascend 910B2\n'
    '\t1                              1                              -                              Mcu                           \n'
    '\t2                              0                              2                              Ascend 910B2\n'
    '\t2                              1                              -                              Mcu                           \n'
    '\t3                              0                              3                              Ascend 910B2\n'
    '\t3                              1                              -                              Mcu                           \n'
    '\t4                              0                              4                              Ascend 910B2\n'
    '\t4                              1                              -                              Mcu                           \n'
    '\t5                              0                              5                              Ascend 910B2\n'
    '\t5                              1                              -                              Mcu                           \n'
    '\t6                              0                              6                              Ascend 910B2\n'
    '\t6                              1                              -                              Mcu                           \n'
    '\t7                              0                              7                              Ascend 910B2\n'
    '\t7                              1                              -                              Mcu                           \n'
)


class InventoryTests(unittest.TestCase):
    def test_retains_chip_and_normalizes_physical_id(self):
        result = inventory.processes(PROCESSES, inventory.mapping(MAP))
        self.assertEqual([(r['device'], r['chip_id'], r['pid']) for r in result], [(4, 0, 111), (7, 0, 222)])

    def test_real_910b2_capture_ignores_padded_mcu_rows(self):
        observed = inventory.mapping(MAP_910B2_CAPTURE)
        self.assertEqual(observed, {(n, 0): {'physical_id': n, 'host_logical_id': n} for n in range(8)})
        processes = inventory.processes(PROCESSES, observed)
        self.assertEqual([(r['device'], r['pid']) for r in processes], [(4, 111), (7, 222)])

    def test_mapping_accepts_trailing_whitespace_with_lf_and_crlf(self):
        expected = {(4, 0): {'physical_id': 4, 'host_logical_id': 0},
                    (7, 0): {'physical_id': 7, 'host_logical_id': 1}}
        for suffix in (' ', '\t', ' \t  '):
            for newline in ('\n', '\r\n'):
                with self.subTest(suffix=repr(suffix), newline=repr(newline)):
                    raw = newline.join(line + suffix for line in MAP.splitlines()) + newline
                    self.assertEqual(inventory.mapping(raw), expected)

    def test_padded_invalid_mapping_still_fails_closed(self):
        invalid = {
            'hardware': MAP.replace('910B3', '910_9391'),
            'chip': MAP.replace('4         0', '4         1'),
            'logical_id': MAP.replace('0                Ascend', '-                Ascend'),
            'duplicate': MAP + '4 0 0 Ascend 910B3\n',
            'mcu_name': MAP.replace('Mcu', 'Mcu extra'),
        }
        for reason, raw in invalid.items():
            with self.subTest(reason=reason):
                padded = '\r\n'.join(line + ' \t' for line in raw.splitlines()) + '\r\n'
                with self.assertRaises(ValueError):
                    inventory.mapping(padded)

    def test_unknown_chip_and_hardware_fail_closed(self):
        with self.assertRaises(ValueError):inventory.processes(PROCESSES.replace('| 4 0', '| 4 1'), inventory.mapping(MAP))
        with self.assertRaises(ValueError):inventory.mapping(MAP.replace('910B3', '910_9391'))
        with self.assertRaises(ValueError):inventory.processes(PROCESSES.replace('Process id', 'PID'), inventory.mapping(MAP))


if __name__ == '__main__':
    unittest.main()
