"""精度消费者使用公开设备CLI，证据路径不得污染前后身份比较。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devices import test_integration
from devices.fixtures import write
from device_contract import ref
from precision.runtime import device_check


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.case = test_integration.IntegrationTests()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        _, handoff = self.case.prepared()
        source = write(self.case.root / 'source-handoff.json', handoff)
        self.inputs = {'handoff': handoff, 'deployment_raw': {
            'evidence': {'handoff': source['path']}, 'detailed_report': {'handoff_sha256': source['sha256']}}}
        self.commands = []

    def execute(self, argv, folder, timeout, **kwargs):
        self.commands.append(argv)
        request = json.loads(Path(argv[argv.index('--request')+1]).read_text())
        output = Path(argv[argv.index('--work-root')+1])
        path = output / 'inspection/verify-agent/attempt/verification-manifest.json'
        workers = self.inputs['handoff']['workers']
        # 原始stat含运行计数，每次观察都变化，但PID/starttime保持不变。
        workers = json.loads(json.dumps(workers))
        for row in workers['npu_processes']:row['stat'] = str(len(self.commands))
        detail = {'workers': workers, 'device_evidence': {key: self.inputs['handoff'][key]
                  for key in ('declared_request', 'device_mapping', 'effective_runtime')}}
        write(path, {'task_id': 'verify-agent', 'operation': 'inspect-devices', 'inputs': request,
                     'status': 'completed', 'success': True, 'conclusions': {'devices': 'pass'}, 'detailed_report': detail})
        folder.mkdir()
        (folder / 'stdout.log').write_text(json.dumps({'report_path': str(path)}))
        return {'returncode': 0}

    def test_new_report_paths_and_raw_stat_do_not_change_service_identity(self):
        with patch.object(device_check, 'execute', side_effect=self.execute):
            before = device_check.inspect(self.inputs, self.case.root, 'before')
            after = device_check.inspect(self.inputs, self.case.root, 'after')
        self.assertEqual(before, after)
        self.assertEqual(self.commands[0][self.commands[0].index('--action')+1], 'inspect-devices')
        self.assertNotIn('report', before)
        self.assertTrue((self.case.root / 'before-device-inspection.json').is_file())
        self.assertTrue((self.case.root / 'after-device-inspection.json').is_file())


if __name__ == '__main__':
    unittest.main()
