"""完整总 Skill 的公开 CLI、搬迁和包外写入回归。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]/'skills/vllm-model-migration'


class CliCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='迁移兼容性 ')
        cls.base = Path(cls.temporary.name).resolve()
        cls.copy = cls.base/'独立 资源'
        shutil.copytree(ROOT, cls.copy)
        (cls.copy/'references/resource-manifest.json').unlink(missing_ok=True)
        cls.original_resources = {p.relative_to(cls.copy).as_posix() for p in cls.copy.rglob('*')
                                  if p.is_file() and '__pycache__' not in p.parts}
        cls.cwd = cls.base/'其他 工作目录'
        cls.cwd.mkdir()
        cls.requests = cls.base/'requests'
        cls.requests.mkdir()
        cls.work = cls.base/'runs'

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def invoke(self, entry, *args):
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.run([sys.executable, '-B', str(entry), *map(str, args)],
                              cwd=self.cwd, env=env, capture_output=True, text=True, timeout=30)

    def write_request(self, name, value):
        path = self.requests/(name+'.json')
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def expected_resources(self):
        added = {'scripts/discovered-test-module.py', 'scripts/copied-test-module.py'}
        return self.original_resources | {p for p in added if (self.copy/p).is_file()}

    def public_entries(self, root):
        return sorted(p for skill in root.rglob('SKILL.md')
                      for p in (skill.parent/'scripts').glob('*.py') if p.name not in (
                          'skill_paths.py', 'service_address.py', 'model_cache.py', 'evalscope_installation.py',
                          'device_contract.py', 'allocation_contract.py', 'device_evidence.py'))

    def request_for(self, skill):
        name = 'vllm-model-migration' if skill == self.copy else skill.name
        fixtures = {
            'vllm-model-prepare': {'source': 'local', 'path': str(self.base/'model'), 'scope': 'metadata'},
            'vllm-dataset-prepare': {'source': 'local', 'path': str(self.base/'data.jsonl'),
                                     'split': 'test', 'format': 'jsonl'},
        }
        if name in fixtures:
            return self.write_request(name, fixtures[name])
        if name == 'vllm-migration-prepare':
            model = self.request_for(next(p.parent for p in self.copy.rglob('SKILL.md')
                                          if p.parent.name == 'vllm-model-prepare'))
            return self.write_request(name, {'purpose': 'assets', 'tasks': {'model': {'request': str(model)}}})
        layout = json.loads((self.copy/'references/skill-layout.json').read_text())
        value = json.loads((skill/layout['skills'][name]['entrypoint']['plan']).read_text())
        return self.write_request(name, value)

    def test_all_public_help_entries_without_pythonpath(self):
        for root in (ROOT, self.copy):
            entries = self.public_entries(root)
            self.assertEqual(len(entries), 21)
            for entry in entries:
                with self.subTest(root=root, entry=entry):
                    result = self.invoke(entry, '--help')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('--venv' if entry.name == 'install_evalscope.py' else '--request', result.stdout)

    def test_all_primary_plans_in_relocated_directory_are_read_only(self):
        layout = json.loads((self.copy/'references/skill-layout.json').read_text())
        for name, row in layout['skills'].items():
            skill = self.copy/row['path']
            entry = skill/row['entrypoint']['script']
            with self.subTest(skill=name):
                request = self.request_for(skill)
                result = self.invoke(entry, '--request', request, '--work-root', self.work,
                                     '--run-id', 'plan-only', '--plan')
                self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
                self.assertFalse(self.work.exists())

    def test_outputs_inside_any_top_skill_resource_area_are_rejected(self):
        layout = json.loads((self.copy/'references/skill-layout.json').read_text())
        for name in layout['skills']:
            skill = self.copy/layout['skills'][name]['path']
            request = self.request_for(skill)
            entry = skill/layout['skills'][name]['entrypoint']['script']
            for area in ('references', 'scripts', 'assets', 'skills'):
                with self.subTest(skill=name, area=area):
                    result = self.invoke(entry, '--request', request, '--work-root', self.copy/area,
                                         '--run-id', 'must-not-write', '--plan')
                    self.assertEqual(result.returncode, 2, result.stderr+result.stdout)
                    self.assertFalse((self.copy/area/'must-not-write').exists())

    def test_auxiliary_plans_do_not_read_referenced_inputs(self):
        register = self.copy/'skills/vllm-migration-execute/skills/vllm-model-register'
        values = [(self.copy/'scripts/check_evalscope_handoff.py', self.write_request('environment-handoff', {
                      'customer_manifest': {'path': '/missing/customer-manifest.json', 'sha256': '0'*64}})),
                  (register/'scripts/check.py', self.request_for(register)),
                  (self.copy/'scripts/export_handoff.py', self.write_request('export', json.loads(
                      (self.copy/'assets/handoff-request.example.json').read_text())))]
        for entry, request in values:
            with self.subTest(entry=entry):
                result = self.invoke(entry, '--request', request, '--work-root', self.work,
                                     '--run-id', 'aux-plan', '--plan')
                self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
                self.assertFalse(self.work.exists())

    def test_relocated_installer_and_customer_previews_never_create_venv_or_business_steps(self):
        task = self.copy / 'assets/template.json'
        source = {'path': str(task), 'sha256': hashlib.sha256(task.read_bytes()).hexdigest()}
        for mode in ('help', 'dry_run'):
            work = self.base / ('preview-runs-' + mode)
            venv = self.base / ('missing-shared-' + mode)
            context = self.write_request('preview-' + mode, {
                'schema_version': '1.0', 'task_source': source,
                'intent': {'deployment': True, 'accuracy': True, 'performance': True, 'conflicts': []},
                'observations': {}, 'evidence': [], 'evalscope_install': {
                    'python': sys.executable, 'venv': str(venv), 'version': '1.2.3', 'upgrade': True, mode: True}})
            result = self.invoke(self.copy / 'scripts/migrate.py', '--task', task, '--context', context,
                                 '--work-root', work, '--run-id', 'preview')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value['status'], 'not_run')
            self.assertFalse(value['success'])
            self.assertEqual(set(value['steps']), {'environment_preview'})
            self.assertEqual(value['steps']['environment_preview']['status'], 'not_run')
            self.assertFalse(venv.exists())
            self.assertFalse((work / 'preview/deliverables').exists())
            if mode == 'dry_run':
                command = value['installation']['plan']['command']
                self.assertIn('--upgrade', command)
                self.assertEqual(command[-1], 'evalscope[perf]==1.2.3')
            resumed = self.invoke(self.copy / 'scripts/migrate.py', '--task', task, '--context', context,
                                  '--work-root', work, '--run-id', 'preview', '--resume', value['evidence']['manifest'])
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertNotEqual(json.loads(resumed.stdout)['evidence']['manifest'], value['evidence']['manifest'])
            self.assertFalse(venv.exists())

    def test_relocated_migration_plan_ignores_install_context(self):
        work = self.base / 'install-plan-runs'
        context = self.write_request('invalid-install-plan', {'evalscope_install': {'upgrade': True}})
        result = self.invoke(self.copy / 'scripts/migrate.py', '--task', self.copy / 'assets/template.json',
                             '--context', context, '--work-root', work, '--run-id', 'plan', '--plan')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
        self.assertFalse(work.exists())

    def test_relocated_all_defaults_plan_and_preview(self):
        task = self.write_request('default-task', {'model': {}, 'environment': {}, 'accuracy': {}})
        before = task.read_bytes()
        work = self.base / 'default-plan-runs'
        plan = self.invoke(self.copy / 'scripts/migrate.py', '--task', task, '--work-root', work,
                           '--run-id', 'defaults', '--plan')
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        result = json.loads(plan.stdout)
        self.assertEqual(result['inputs']['environment']['available_npu_devices'], -1)
        self.assertEqual([row['name'] for row in result['inputs']['accuracy']['datasets']], ['ceval', 'gsm8k'])
        self.assertFalse(work.exists())
        venv = self.base / 'default-preview-venv'
        context = self.write_request('default-preview-context', {'schema_version': '1.0',
            'task_source': {'path': str(task), 'sha256': hashlib.sha256(before).hexdigest()},
            'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []},
            'observations': {}, 'evidence': [], 'evalscope_install': {'help': True, 'venv': str(venv)}})
        preview = self.invoke(self.copy / 'scripts/migrate.py', '--task', task, '--context', context,
                              '--work-root', work, '--run-id', 'defaults')
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        result = json.loads(preview.stdout)
        self.assertEqual(result['status'], 'not_run')
        self.assertEqual(set(result['steps']), {'environment_preview'})
        self.assertFalse(venv.exists())
        self.assertEqual(task.read_bytes(), before)

    def test_customer_plans_in_relocated_directory_do_not_prepare_assets(self):
        task = self.copy/'assets/template.json'
        result = self.invoke(self.copy/'scripts/migrate.py', '--task', task,
                             '--work-root', self.work, '--run-id', 'customer-plan', '--plan')
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
        self.assertFalse(self.work.exists())
        entries = [
            ('skills/vllm-migration-prepare/scripts/customer.py', {'operation': 'environment'}),
            ('skills/vllm-migration-prepare/scripts/customer.py', {'operation': 'evalscope_environment'}),
            ('skills/vllm-migration-execute/skills/verify-agent/scripts/customer.py', {'operation': 'observe'}),
            ('skills/vllm-precision-align/skills/verify-agent-plus/scripts/catalog.py', {
                'model_path': str(self.base/'absent-model'), 'dataset': {'path': str(self.base/'absent-data')}}),
        ]
        for index, (entry, value) in enumerate(entries):
            with self.subTest(entry=entry):
                source = self.write_request('customer-aux-'+str(index), value)
                result = self.invoke(self.copy/entry, '--request', source, '--work-root', self.work,
                                     '--run-id', 'customer-aux-plan', '--plan')
                self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
                self.assertFalse(self.work.exists())

    def test_blocked_model_does_not_prevent_independent_local_data_preparation(self):
        data = self.base/'samples.jsonl'
        data.write_text('{"question":"synthetic", "answer":"test"}\n')
        dataset = self.write_request('independent-data', {
            'source': 'local', 'path': str(data), 'split': 'test', 'format': 'jsonl'})
        request = self.write_request('partial-stage', {'purpose': 'assets', 'tasks': {
            'model': {'request': str(self.requests/'absent-model.json')},
            'dataset': {'request': str(dataset)}}})
        entry = self.copy/'skills/vllm-migration-prepare/scripts/prepare.py'
        result = self.invoke(entry, '--request', request, '--work-root', self.base/'partial-runs',
                             '--run-id', 'partial')
        self.assertEqual(result.returncode, 3, result.stderr+result.stdout)
        manifest = json.loads(Path(json.loads(result.stdout)['manifest']).read_text())
        self.assertEqual(manifest['tasks']['model']['status'], 'blocked')
        self.assertEqual(manifest['tasks']['dataset']['status'], 'completed')
        self.assertEqual(manifest['status'], 'blocked')

    def test_missing_task_entry_blocks_only_its_consumer(self):
        source = self.base/'missing-entry-source'
        shutil.copytree(self.copy, source)
        (source/'skills/vllm-migration-prepare/skills/vllm-model-prepare/scripts/prepare.py').unlink()
        data = self.base/'entry-data.jsonl'
        data.write_text('{"question":"synthetic", "answer":"test"}\n')
        dataset = self.write_request('entry-dataset', {
            'source': 'local', 'path': str(data), 'split': 'test', 'format': 'jsonl'})
        model = self.write_request('entry-model', {
            'source': 'local', 'path': str(self.base/'missing-model'), 'scope': 'metadata'})
        request = self.write_request('missing-entry', {'purpose': 'assets', 'tasks': {
            'model': {'request': str(model)}, 'dataset': {'request': str(dataset)}}})
        entry = source/'skills/vllm-migration-prepare/scripts/prepare.py'
        result = self.invoke(entry, '--request', request, '--work-root', self.base/'entry-runs',
                             '--run-id', 'missing-entry')
        self.assertEqual(result.returncode, 3, result.stderr+result.stdout)
        manifest = json.loads(Path(json.loads(result.stdout)['manifest']).read_text())
        self.assertEqual(manifest['tasks']['model']['status'], 'blocked')
        self.assertEqual(manifest['tasks']['dataset']['status'], 'completed')

    def test_preparation_plan_works_with_no_git_available(self):
        skill = self.copy/'skills/vllm-migration-prepare'
        request = self.request_for(skill)
        environment = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        environment.update(PATH='', PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(skill/'scripts/prepare.py'),
            '--request', str(request), '--work-root', str(self.work), '--run-id', 'no-git', '--plan'],
            cwd=self.cwd, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        self.assertFalse(self.work.exists())

    def test_legacy_flat_resources_can_be_copied_and_verified(self):
        root = self.base/'legacy-flat'
        source = root/'vllm-model-register/scripts/check.py'
        source.parent.mkdir(parents=True)
        source.write_text('# legacy static check')
        destination = self.base/'legacy-copy'
        scripts = self.copy/'skills/vllm-migration-execute/skills/verify-agent/scripts'
        code = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from verification.runtime.registration import copy_resources
from skill_paths import registration_files, resource_snapshot
root, target = Path(sys.argv[2]), Path(sys.argv[3])
files = resource_snapshot(registration_files(root), root)
copy_resources(root, files, target)
assert resource_snapshot(registration_files(target), target) == files
'''
        result = self.invoke('-c', code, scripts, root, destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination/'vllm-model-register/scripts/check.py').read_bytes(), source.read_bytes())

    def test_local_metadata_prepare_publishes_complete_resource_evidence(self):
        model = self.base/'metadata-model'
        model.mkdir(exist_ok=True)
        (model/'config.json').write_text('{"model_type":"synthetic-test"}')
        request = self.write_request('local-stage', {'purpose': 'assets', 'tasks': {'model': {
            'request': str(self.write_request('local-model', {
                'source': 'local', 'path': str(model), 'scope': 'metadata'}))}}})
        skill = self.copy/'skills/vllm-migration-prepare'
        result = self.invoke(skill/'scripts/prepare.py', '--request', request,
                             '--work-root', self.base/'local-runs', '--run-id', 'metadata')
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        manifest = json.loads(Path(json.loads(result.stdout)['manifest']).read_text())
        resources = manifest['resources']
        self.assertIn('scripts/skill_paths.py', resources)
        self.assertIn('references/skill-layout.json', resources)
        self.assertIn('references/task-result.schema.json', resources)
        for relative, digest in resources.items():
            self.assertEqual(hashlib.sha256((self.copy/relative).read_bytes()).hexdigest(), digest)

    def test_preparation_records_added_and_changed_sources_without_rewriting_history(self):
        added = self.copy/'scripts/discovered-test-module.py'
        added.write_text('# first static content')
        model = self.base/'snapshot-model'
        model.mkdir()
        (model/'config.json').write_text('{"model_type":"synthetic-test"}')
        request = self.write_request('snapshot-stage', {'purpose': 'assets', 'tasks': {'model': {
            'request': str(self.write_request('snapshot-model', {
                'source': 'local', 'path': str(model), 'scope': 'metadata'}))}}})
        entry = self.copy/'skills/vllm-migration-prepare/scripts/prepare.py'
        snapshots = []
        for run_id in ('snapshot-first', 'snapshot-second'):
            result = self.invoke(entry, '--request', request, '--work-root', self.base/'snapshot-runs',
                                 '--run-id', run_id)
            self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
            path = Path(json.loads(result.stdout)['manifest'])
            snapshots.append((path, path.read_bytes()))
            resources = json.loads(path.read_text())['resources']
            self.assertEqual(resources[added.relative_to(self.copy).as_posix()],
                             hashlib.sha256(added.read_bytes()).hexdigest())
            added.write_text('# second static content')
        self.assertNotEqual(json.loads(snapshots[0][1])['resources'], json.loads(snapshots[1][1])['resources'])
        self.assertEqual(snapshots[0][0].read_bytes(), snapshots[0][1])

    def test_both_registration_copy_paths_reject_source_target_and_file_set_changes(self):
        code = '''import sys
from pathlib import Path
from unittest.mock import patch
root, target = Path(sys.argv[1]), Path(sys.argv[2])
chain, mutation = sys.argv[3:5]
if chain == 'execute':
    sys.path.insert(0, str(root/'skills/vllm-migration-execute/scripts'))
    from execution import transport as module
    operation = lambda: module.copy_resources(target)
else:
    sys.path.insert(0, str(root/'skills/vllm-migration-execute/skills/verify-agent/scripts'))
    from verification.runtime import registration as module
    from skill_paths import registration_files, resource_snapshot
    expected = resource_snapshot(registration_files(root), root)
    operation = lambda: module.copy_resources(root, expected, target)
original = module.shutil.copy2
changed = False
def copy(source, destination):
    global changed
    result = original(source, destination)
    if not changed:
        changed = True
        if mutation == 'source': Path(source).write_bytes(Path(source).read_bytes()+b'changed')
        elif mutation == 'target': Path(destination).write_bytes(b'changed')
        else: (root/'scripts/new-during-copy.py').write_text('# added during copy')
    return result
with patch.object(module.shutil, 'copy2', side_effect=copy):
    try: operation()
    except ValueError: print('rejected changed resource snapshot')
    else: raise AssertionError('changed resources accepted')
'''
        for chain in ('execute', 'verify'):
            for mutation in ('source', 'target', 'added'):
                with self.subTest(chain=chain, mutation=mutation):
                    source = self.base/(chain+'-'+mutation+'-source')
                    shutil.copytree(self.copy, source)
                    target = self.base/(chain+'-'+mutation+'-target')
                    result = self.invoke('-c', code, source, target, chain, mutation)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('rejected changed resource snapshot', result.stdout)

    def test_registration_copy_contains_only_declared_business_resources(self):
        extra = self.copy/'unlisted-output.json'
        extra.write_text('{}')
        (self.copy/'scripts/copied-test-module.py').write_text('# new static file')
        destination = self.base/'registration-copy'
        scripts = self.copy/'skills/vllm-migration-execute/scripts'
        code = ('import sys; from pathlib import Path; '
                'sys.path.insert(0, sys.argv[1]); '
                'from execution.transport import copy_resources; '
                'copy_resources(Path(sys.argv[2]))')
        result = self.invoke('-c', code, scripts, destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((destination/extra.name).exists())
        self.assertEqual({p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()},
                         self.expected_resources())
        check = next(p for p in destination.rglob('check.py') if p.parent.name == 'scripts')
        self.assertEqual(self.invoke(check, '--help').returncode, 0)

    def test_deployment_registration_persists_actual_copy_identity(self):
        scripts = self.copy/'skills/vllm-migration-execute/scripts'
        attempt = self.base/'recorded-registration'
        attempt.mkdir()
        code = '''import hashlib,json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from execution import transport
attempt = Path(sys.argv[2])
def call(argv, *args):
    root = argv[argv.index('--work-root')+1]
    return 0, json.dumps({'manifest':root+'/child/registration-manifest.json'})
def transfer(argv, parent, label):
    if label == 'register-evidence-copy':
        target = Path(argv[-1])/'child/registration-manifest.json'
        target.parent.mkdir()
        target.write_text('{}')
with patch.object(transport,'inspect'), patch.object(transport,'transfer',side_effect=transfer), \\
     patch.object(transport,'call',side_effect=call), patch.object(transport,'plan',return_value={}), \\
     patch.object(transport,'reply',side_effect=json.loads):
    transport.execute({'container_id':'synthetic','python':sys.executable},{},attempt,'copy',10)
observed = json.loads((attempt/'registration-resource-identity.json').read_text())
copied = attempt/'registration-resources'
assert observed == {p.relative_to(copied).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in copied.rglob('*') if p.is_file()}
print('recorded actual copied byte identity')
'''
        result = self.invoke('-c', code, scripts, attempt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('recorded actual copied byte identity', result.stdout)

    def test_carrier_registration_copies_the_same_declared_file_set(self):
        (self.copy/'unlisted-carrier-output.json').write_text('{}')
        (self.copy/'scripts/copied-test-module.py').write_text('# new static file')
        attempt = self.base/'carrier-attempt'
        attempt.mkdir()
        checked = self.write_request('carrier-check', {'model': {'architecture': 'Synthetic',
            'config': '/model/config.json'}, 'implementation': {'path': '/sdk/model.py', 'sha256': '0'*64},
            'target': {'registry_path': '/sdk/registry.py', 'registry_sha256': '0'*64}})
        request = self.write_request('carrier', {'registration': {'resources': str(self.copy),
            'request': str(checked)}, '_architectures': ['Synthetic'],
            'model': {'runtime_root': '/model', 'config': 'config.json'},
            'runtime': {'files': {'/sdk/model.py': '0'*64, '/sdk/registry.py': '0'*64}}})
        scripts = self.copy/'skills/vllm-migration-execute/skills/verify-agent/scripts'
        code = '''import json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from verification.runtime import registration
copied = []
def capture(argv, *args):
    source = Path(argv[2])
    if source.is_dir():
        copied.extend(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())
with patch.object(registration, 'execute'), patch.object(registration, 'run', side_effect=capture):
    registration.stage(json.loads(Path(sys.argv[2]).read_text()),
        {'kind': 'docker', 'nonce': 'synthetic', 'identity': {'container_id': 'fixture'}},
        [], Path(sys.argv[3]))
print(json.dumps(sorted(copied)))
'''
        result = self.invoke('-c', code, scripts, request, attempt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(json.loads(result.stdout)), self.expected_resources())


if __name__ == '__main__':
    unittest.main()
