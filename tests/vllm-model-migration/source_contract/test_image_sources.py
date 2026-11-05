"""真实公开计划入口；Docker 仅在镜像探测边界模拟，不运行模型。"""
import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))

from customer_workflow import effects, runner
from customer_workflow.compile import deployment
from coordination.runtime import sources


class ImageSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='来源 契约 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.asset = {'path': str(self.root / 'model'), 'metadata': {'config': {}}}
        sample = ROOT / 'skills/vllm-migration-execute/skills/vllm-model-precheck/assets/request.example.json'
        target = json.loads(sample.read_text())['target']
        self.observations = {'target': target, 'deployment': {'dtype': 'bfloat16', 'reviews': [],
            'sources': {key: {'kind': 'installed_package', 'path': str(self.root / key),
                             'runtime_path': '/container/' + key, 'distribution': distribution,
                             'version': '0.20.2+empty', 'image_id': 'sha256:' + 'a' * 64,
                             'expected_identity': 'b' * 64}
                        for key, distribution in [('vllm', 'vllm'), ('vllm_ascend', 'vllm-ascend')]}}}
        self.session = Mock(attempt=self.root / 'attempt', value={'run_id': 'sources', 'steps': {}})
        self.session.artifact.side_effect = self.artifact

    def artifact(self, label, value):
        path = self.root / (label + '.json')
        path.write_text(json.dumps(value))
        return sources.ref(path)

    def test_actual_image_inspection_output_reaches_public_precheck_plan(self):
        from verification.customer import image_probe
        image = 'sha256:' + 'a' * 64
        probe = {'sources': {key: {'path': '/container/' + key, 'distribution': distribution,
                                  'version': '0.20.2+empty'}
                             for key, distribution in [('vllm', 'vllm'), ('vllm_ascend', 'vllm-ascend')]}}
        def docker_run(argv, *args, **kwargs):
            if argv[1] == 'create':
                self.nonce = argv[argv.index('--label') + 1].split('=', 1)[1]
                return 'c' * 64
            if argv[1] == 'start':
                return json.dumps(probe)
            if argv[1] == 'cp':
                path = Path(argv[-1]); path.mkdir()
                (path / '__init__.py').write_text('# synthetic source\n')
            return ''
        (self.root / 'image').mkdir()
        with patch.object(image_probe, 'local_daemon'), patch.object(image_probe, 'run', side_effect=docker_run), \
                patch.object(image_probe.docker, 'inspect', side_effect=lambda *a: {
                    'image_id': image, 'labels': {'vllm.verify.probe': self.nonce}}):
            observed = image_probe.inspect({'image_id': image}, self.root / 'image')
        self.observations['deployment']['sources'] = observed['sources']
        original = copy.deepcopy(self.observations)
        compiled = self.artifact('compiled', deployment.precheck(self.asset, self.observations))
        cli = ROOT / 'skills/vllm-migration-execute/skills/vllm-model-precheck/scripts/precheck.py'
        result = subprocess.run([sys.executable, '-B', str(cli), '--request', compiled['path'],
                                 '--work-root', str(self.root / 'work'), '--run-id', 'sources', '--plan'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        reference = effects.precheck_plan(self.session, self.asset, self.observations, self.root / 'work')
        request = sources.bound(reference)
        self.assertTrue(all('runtime_path' not in row for row in request['sources'].values()))
        projection = json.loads((self.root / 'precheck-sources.json').read_text())
        self.assertEqual(projection['runtime_paths'], {'vllm': '/container/vllm', 'vllm_ascend': '/container/vllm_ascend'})
        self.assertEqual(sources.bound(projection['source']), observed['sources'])
        self.assertEqual(self.observations, original)
        self.assertFalse((self.root / 'work').exists())
        self.assertEqual(self.session.step.call_args.args[:2], ('precheck_plan', 'not_run'))

    def test_git_and_legacy_installed_sources_remain_accepted(self):
        for kind in ('git', 'installed_package'):
            with self.subTest(kind=kind):
                observed = copy.deepcopy(self.observations)
                for row in observed['deployment']['sources'].values():
                    row.pop('runtime_path')
                    if kind == 'git':
                        path = row['path']; row.clear()
                        row.update(path=path, revision='a' * 40)
                effects.precheck_plan(self.session, self.asset, observed, self.root / 'work')
                self.assertEqual(json.loads((self.root / 'precheck-sources.json').read_text())['runtime_paths'], {})

    def test_unknown_fields_and_invalid_runtime_paths_are_rejected_without_mutation(self):
        for changes in ({'runtime_paht': '/wrong'}, {'runtime_path': ''}, {'runtime_path': 'relative'},
                        {'runtime_path': '/a/../b'}, {'runtime_path': None}, {'runtime_path': 3}):
            with self.subTest(changes=changes):
                observed = copy.deepcopy(self.observations)
                observed['deployment']['sources']['vllm'].update(changes)
                original = copy.deepcopy(observed)
                with self.assertRaisesRegex(ValueError, 'sources.vllm'):
                    deployment.precheck(self.asset, observed)
                self.assertEqual(observed, original)

    def test_public_plan_rejects_missing_identity_with_saved_diagnostic(self):
        del self.observations['deployment']['sources']['vllm']['image_id']
        with self.assertRaisesRegex(ValueError, '镜像身份'):
            effects.precheck_plan(self.session, self.asset, self.observations, self.root / 'work')
        logs = list(self.session.attempt.rglob('*.stderr.txt'))
        self.assertEqual(len(logs), 1)
        self.assertIn('镜像身份', logs[0].read_text())
        self.assertEqual(self.session.step.call_args.args[:2], ('precheck_plan', 'blocked'))

    def test_invalid_precheck_blocks_before_functional_allocation_or_container_effects(self):
        self.session.ready.side_effect = lambda key: {'prepared': True} if key == 'preparation' else None
        self.observations['deployment']['sources']['vllm']['version'] = ''
        with patch.object(deployment, 'prepared_model', return_value=self.asset), \
                patch.object(effects, 'functional_inputs', side_effect=AssertionError('不应执行功能观察')) as functional, \
                patch.object(effects, 'operation') as operation, patch.object(runner, 'execute') as execute:
            with self.assertRaisesRegex(ValueError, 'version'):
                runner.migrate(self.session, {}, self.observations, [], self.root / 'work', {})
            functional.assert_not_called()
            operation.assert_not_called()
            execute.assert_not_called()

    def test_successful_retry_clears_old_failure_and_keeps_plan_as_not_run(self):
        from customer_workflow.state import Session
        self.session.step.side_effect = lambda *a, **kw: Session.step(self.session, *a, **kw)
        source = self.observations['deployment']['sources']['vllm']
        version = source.pop('version')
        with self.assertRaises(ValueError):
            effects.precheck_plan(self.session, self.asset, self.observations, self.root / 'work')
        source['version'] = version
        effects.precheck_plan(self.session, self.asset, self.observations, self.root / 'work')
        row = self.session.value['steps']['precheck_plan']
        self.assertEqual(row['status'], 'not_run')
        self.assertIsNone(row.get('diagnostic'))
        self.assertEqual(sources.bound(row['receipt'])['status'], 'not_run')

    def test_malformed_plan_or_cancellation_never_leaves_running_state(self):
        from customer_workflow.state import Session
        self.session.step.side_effect = lambda *a, **kw: Session.step(self.session, *a, **kw)
        for output in ('{}', '{broken', '{"status":"completed","request":{}}'):
            with self.subTest(output=output), patch.object(effects, 'call', return_value=(0, output)):
                with self.assertRaises(ValueError):
                    effects.precheck_plan(self.session, self.asset, self.observations, self.root / 'work')
                self.assertEqual(self.session.value['steps']['precheck_plan']['status'], 'blocked')
        with patch.object(effects, 'call', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                effects.precheck_plan(self.session, self.asset, self.observations, self.root / 'work')
        self.assertEqual(self.session.value['steps']['precheck_plan']['status'], 'failed')

    def test_git_runtime_path_and_invalid_source_shapes_are_rejected(self):
        for source in (None, [], {'other': {}}, {'vllm': [], 'vllm_ascend': {}},
                       {'vllm': {'kind': 'git', 'runtime_path': '/container/vllm'}, 'vllm_ascend': {}}):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'sources'):
                deployment.source_projection(source)


if __name__ == '__main__':
    unittest.main()
