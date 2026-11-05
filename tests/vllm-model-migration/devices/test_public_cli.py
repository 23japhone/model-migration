"""公开CLI计划不探测设备，不修改旧交接。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from device_contract import ref


class PublicTests(unittest.TestCase):
    def test_inspect_devices_plan_does_not_read_handoff_or_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            request = root / 'request.json'
            request.write_text(json.dumps({'handoff': {'path': str(root / 'missing.json'), 'sha256': 'a'*64}}))
            argv = [sys.executable, '-B', str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts/verify.py'),
                    '--action', 'inspect-devices', '--request', str(request), '--work-root', str(root / 'out'),
                    '--run-id', 'check', '--plan']
            result = subprocess.run(argv, text=True, capture_output=True, timeout=15, cwd='/')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
            self.assertFalse((root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
