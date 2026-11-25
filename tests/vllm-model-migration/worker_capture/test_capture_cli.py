"""公开入口的静态请求、缺失证据与独立回查。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py'))


class CaptureCLI(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def cli(self, action, options, plan=False):
        path = self.root / 'request.json'
        path.write_text(json.dumps(dict(source={'path': '/missing.json', 'sha256': '0'*64},
                                       request_id='one', **options)))
        return subprocess.run([sys.executable, '-B', str(VERIFY), '--action', action, '--request', str(path),
                               '--run-id', 'one', '--work-root', str(self.root/'evidence'), *(['--plan'] if plan else [])],
                              capture_output=True, text=True)

    def test_plans_no_effects(self):
        for action, options in [('capture-one', {'prompt':'abc'}), ('capture-read', {})]:
            result = self.cli(action, options, plan=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
        self.assertFalse((self.root/'evidence').exists())

    def test_rejects_invalid_query_or_unbound_input(self):
        self.assertEqual(self.cli('capture-one', {'prompt':'x','max_tokens':1}, plan=True).returncode, 2)
        self.assertEqual(self.cli('capture-read', {'output_index': -1}, plan=True).returncode, 2)
        self.assertEqual(self.cli('capture-one', {'prompt':'x'}).returncode, 1)
