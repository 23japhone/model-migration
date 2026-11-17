"""多 CLI 操作之间的租约、请求身份与互斥；没有真实模型推理。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))


class SessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_public_plan_has_no_effects(self):
        path = self.root / 'request.json'
        path.write_text(json.dumps({'operation': 'begin', 'source': {'path': '/missing/report.json', 'sha256': 'a'*64},
                                    'owner_pid': os.getpid()}))
        result = subprocess.run([sys.executable, '-B', str(VERIFY), '--action', 'capture-session',
                                 '--request', str(path), '--work-root', str(self.root/'out'), '--run-id', 's', '--plan'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root/'out').exists())

    def test_lease_survives_cli_boundary_and_refuses_duplicate_arm(self):
        from verification.runtime.capture import session
        from verification.evidence import write_json
        from verification.runtime.capture.tensors import file_ref
        config = self.root/'config.json'; write_json(config, {'dump_path': str(self.root/'dump'), 'dump_enable': False})
        prep = {'config': file_ref(config, self.root), 'dump': {'runtime_path': str(self.root/'dump')}}
        handoff = {'worker_capture': {'receipt': prep['config']}}
        ready = {'coverage_capability': 'modules_and_registered_apis', 'rank': 0}
        self.enterContext(patch.object(session, 'deployment', return_value=({}, handoff, prep)))
        self.enterContext(patch.object(session, 'live', return_value=ready))
        request = {'operation': 'begin', 'source': prep['config'], 'owner_pid': os.getpid()}
        code, result = session.run(request, self.root/'out', 's')
        self.assertEqual(code, 0)
        reference = result['session']
        self.assertTrue((self.root/'lease.json').exists())
        with self.assertRaisesRegex(ValueError, '租约'):
            session.run(request, self.root/'other', 's')
        arm = {'operation': 'arm', 'source': reference, 'sample_key': 'sample-a', 'native_id': 2, 'trace_id': 'trace-a'}
        code, value = session.run(arm, self.root/'out', 's')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.root/'active.json').read_text())['directory'], value['directory'])
        with self.assertRaisesRegex(ValueError, '活动'):
            session.run({**arm, 'trace_id': 'trace-b'}, self.root/'out', 's')
        code, _ = session.run({'operation': 'end', 'source': reference}, self.root/'out', 's')
        self.assertEqual(code, 0)
        self.assertFalse((self.root/'active.json').exists())
        self.assertFalse((self.root/'lease.json').exists())

    def test_foreign_or_stale_lease_not_removed(self):
        from verification.runtime.capture import session
        lease = self.root/'lease.json'; lease.write_text('{"nonce":"foreign"}')
        with session.locked(self.root):
            with self.assertRaisesRegex(ValueError, '租约'):
                session.no_lease(self.root)
        self.assertTrue(lease.exists())

    def test_live_failure_keeps_attempt_evidence_without_creating_lease(self):
        from verification.runtime.capture import session
        from verification.evidence import write_json
        from verification.runtime.capture.tensors import file_ref
        config = self.root/'config.json'; write_json(config, {})
        prep = {'config': file_ref(config, self.root)}
        def fail_live(handoff, preparation, attempt):
            write_json(attempt/'probe.json', {'error': 'worker replaced'})
            raise ValueError('worker replaced')
        with patch.object(session, 'deployment', return_value=({}, {}, prep)), patch.object(session, 'live', fail_live):
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'worker replaced'):
                    session.run({'operation':'begin','source':prep['config'],'owner_pid':os.getpid()},
                                self.root/'fresh/out', 'fresh')
        self.assertEqual(len(list((self.root/'fresh/out').glob('*/probe.json'))), 2)
        self.assertFalse((self.root/'lease.json').exists())
