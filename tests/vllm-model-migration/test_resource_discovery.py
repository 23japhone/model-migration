"""不依赖构建清单的资源发现、身份和安全边界。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from test_skill_paths import ROOT, load_paths


class ResourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='资源 枚举 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()/'独立 Skill'
        shutil.copytree(ROOT, self.root)
        (self.root/'references/resource-manifest.json').unlink(missing_ok=True)
        self.paths = load_paths()
        patched = patch.object(self.paths, 'plugin_root', return_value=self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def test_bundle_works_without_generated_manifest(self):
        files = self.paths.bundle_files()
        expected = {p for p in self.root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        self.assertEqual(set(files), expected)
        self.assertEqual(files, sorted(files))

    def test_new_source_and_reference_files_are_discovered(self):
        added = [self.root/'scripts/new-static-module.py', self.root/'references/new-note.md']
        for path in added:
            path.write_text('# synthetic resource')
        self.assertTrue(set(added).issubset(self.paths.owned_files(self.root)))
        self.assertTrue(set(added).issubset(self.paths.bundle_files()))

    def test_resource_snapshot_records_current_bytes_after_change(self):
        path = self.root/'references/contract.md'
        before = self.paths.resource_snapshot(self.paths.owned_files(self.root))
        path.write_text(path.read_text()+'\nSynthetic test change.\n')
        after = self.paths.resource_snapshot(self.paths.owned_files(self.root))
        key = path.relative_to(self.root).as_posix()
        self.assertNotEqual(before[key], after[key])
        self.assertEqual(after[key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(all(isinstance(k, str) and isinstance(v, str) and len(v) == 64
                            for k, v in after.items()))

    def test_removed_optional_resource_is_no_longer_listed(self):
        path = self.root/'assets/entry-analyze.example.json'
        path.unlink()
        self.assertNotIn(path, self.paths.owned_files(self.root))
        self.assertNotIn(path, self.paths.bundle_files())

    def test_missing_shared_and_declared_entry_resources_are_rejected(self):
        required = ['SKILL.md', 'scripts/migrate.py', 'scripts/export_handoff.py', 'scripts/check_evalscope_handoff.py',
                    'scripts/skill_paths.py', 'scripts/allocation_contract.py', 'scripts/device_evidence.py',
                    'references/skill-layout.json',
                    'references/task-result.schema.json',
                    'skills/vllm-migration-prepare/skills/vllm-dataset-prepare/references/recipes.json',
                    'skills/vllm-migration-execute/skills/vllm-model-register/scripts/check.py',
                    'skills/vllm-migration-execute/skills/vllm-model-register/assets/plugin_callback.py.template']
        for relative in required:
            path = self.root/relative
            content = path.read_bytes()
            path.unlink()
            try:
                with self.subTest(relative=relative), self.assertRaisesRegex((ValueError, OSError), re.escape(path.name)):
                    self.paths.bundle_files()
            finally:
                path.write_bytes(content)

    def test_missing_unselected_task_does_not_block_selected_owner(self):
        dataset = self.paths.skill_root('vllm-dataset-prepare')
        (dataset/'scripts/prepare.py').unlink()
        model = self.paths.skill_root('vllm-model-prepare')
        self.assertTrue(self.paths.owned_files(model))
        with self.assertRaises((ValueError, OSError)):
            self.paths.bundle_files()

    def test_caches_logs_and_maintenance_are_excluded(self):
        ignored = ['scripts/__pycache__/module.pyc', 'scripts/.pytest_cache/state.json',
                   'assets/.DS_Store', 'scripts/debug.log', 'unlisted-output.json',
                   'docs/private.md', '.git/config', '.agents/skills/private/SKILL.md']
        for relative in ignored:
            path = self.root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('not a runtime resource')
        files = self.paths.bundle_files()
        self.assertFalse(any(self.root/path in files for path in ignored))

    def test_unrecognized_files_inside_static_roles_are_rejected(self):
        for relative in ('scripts/private.env', 'assets/model.safetensors'):
            path = self.root/relative
            path.write_text('unexpected file')
            try:
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    self.paths.bundle_files()
            finally:
                path.unlink()

    def test_new_symlink_inside_resource_role_is_rejected(self):
        outside = self.root.parent/'outside.py'
        outside.write_text('outside')
        (self.root/'scripts/linked.py').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.paths.bundle_files()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX FIFO support')
    def test_special_file_is_rejected_without_opening_it(self):
        os.mkfifo(self.root/'scripts/special.py')
        with self.assertRaises(ValueError):
            self.paths.bundle_files()

    def test_unknown_nested_skill_is_not_silently_omitted(self):
        path = self.root/'skills/unknown-skill'
        path.mkdir()
        (path/'SKILL.md').write_text('# unknown')
        with self.assertRaises(ValueError):
            self.paths.bundle_files()

    def test_layout_root_cannot_omit_the_shared_resource_root(self):
        other = self.root/'unexpected-root'
        (other/'scripts').mkdir(parents=True)
        (other/'SKILL.md').write_text('# unexpected root')
        for name in ('migrate.py', 'export_handoff.py'):
            (other/'scripts'/name).write_text('# synthetic entry')
        path = self.root/'references/skill-layout.json'
        value = json.loads(path.read_text())
        value['skills'][value['root']]['path'] = 'unexpected-root'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.paths.bundle_files()

    def test_direct_file_access_cannot_bypass_resource_policy(self):
        path = self.root/'assets/private.env'
        path.write_text('unexpected')
        with self.assertRaises(ValueError):
            self.paths.owned_files(path)


if __name__ == '__main__':
    unittest.main()
