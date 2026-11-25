"""总 Skill 静态资源契约与路径边界回归；只使用标准库。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]/'skills/vllm-model-migration'


def load_paths():
    spec = importlib.util.spec_from_file_location('tested_skill_paths', ROOT/'scripts/skill_paths.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillPathsTests(unittest.TestCase):
    def setUp(self):
        self.paths = load_paths()

    def test_layout_resolves_all_fourteen_skills_and_public_entries(self):
        layout = self.paths.read_index('skill-layout.json')
        self.assertEqual(len(layout['skills']), 14)
        self.assertEqual(self.paths.skill_root(layout['root']), ROOT)
        for name, row in layout['skills'].items():
            with self.subTest(skill=name):
                skill = self.paths.skill_root(name)
                self.assertTrue((skill/'SKILL.md').is_file())
                self.assertTrue((skill/row['entrypoint']['script']).is_file())
                if row['parent']:
                    parent = self.paths.skill_root(row['parent'])
                    self.assertEqual(skill.parent, parent/'skills')
                    self.assertEqual(row['level'], layout['skills'][row['parent']]['level']+1)

    def test_bundle_covers_runtime_files_and_current_bytes(self):
        files = self.paths.bundle_files()
        expected = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts}
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual({p.relative_to(ROOT).as_posix() for p in files}, expected)
        snapshot = self.paths.resource_snapshot(files)
        for key, digest in snapshot.items():
            with self.subTest(resource=key):
                self.assertEqual(digest, hashlib.sha256((ROOT/key).read_bytes()).hexdigest())

    def test_direct_ownership_partitions_resources_without_child_duplicates(self):
        layout = self.paths.read_index('skill-layout.json')
        owned = []
        for name in layout['skills']:
            skill = self.paths.skill_root(name)
            files = self.paths.owned_files(skill)
            self.assertTrue(files, name)
            self.assertFalse(any(p.is_relative_to(skill/'skills') for p in files), name)
            owned.extend(files)
        self.assertEqual(len(owned), len(set(owned)))
        expected = set(self.paths.bundle_files())
        self.assertEqual(set(owned), expected)
        self.assertIn(ROOT/'scripts/skill_paths.py', self.paths.owned_files(ROOT))
        self.assertEqual(self.paths.shared_resource('references/task-result.schema.json'),
                         ROOT/'references/task-result.schema.json')

    def test_relative_paths_keep_normalization_and_traversal_guards(self):
        for value in ('.', '..', '../outside', '/absolute', 'a/../b', 'a//b', './a', 'a\\b', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.paths.contained(value)
        with self.assertRaises(ValueError):
            self.paths.skill_root('uninstalled')

    def test_index_parser_rejects_duplicate_fields_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root/'references').mkdir()
            with patch.object(self.paths, 'plugin_root', return_value=root):
                for text in ('{"root":1,"root":2}', '{"x":NaN}', '{"x":Infinity}'):
                    (root/'references/skill-layout.json').write_text(text)
                    with self.subTest(text=text), self.assertRaises(ValueError):
                        self.paths.read_index('skill-layout.json')

    def test_symlink_and_outside_inventory_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base/'resource'
            root.mkdir()
            (base/'outside').write_text('outside')
            (root/'link').symlink_to(base/'outside')
            with patch.object(self.paths, 'plugin_root', return_value=root):
                with self.assertRaises(ValueError):
                    self.paths.contained('link')
                with self.assertRaises(ValueError):
                    self.paths.owned_files(base/'outside')

    def test_nested_registration_uses_the_supplied_bundle_layout(self):
        with tempfile.TemporaryDirectory(prefix='注册 副本 ') as temporary:
            root = Path(temporary).resolve()
            (root/'references').mkdir()
            (root/'skills/custom-register/scripts').mkdir(parents=True)
            entry = 'skills/custom-register/scripts/check.py'
            (root/entry).write_text('# copied check entry')
            layout = {'root': 'vllm-model-migration', 'skills': {
                'vllm-model-register': {'path': 'skills/custom-register'}}}
            (root/'references/skill-layout.json').write_text(json.dumps(layout))
            self.assertEqual(self.paths.registration_bundle(root), (root, entry))
            self.assertEqual(self.paths.registration_bundle(root/'skills'), (root, entry))

    def test_legacy_flat_registration_remains_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            entry = 'vllm-model-register/scripts/check.py'
            (root/entry).parent.mkdir(parents=True)
            (root/entry).write_text('# legacy check entry')
            self.assertEqual(self.paths.registration_bundle(root), (root, entry))
            (root/entry).unlink()
            with self.assertRaises(ValueError):
                self.paths.registration_bundle(root)

    def test_bundle_inventory_ignores_output_outside_resource_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()/'迁移 副本'
            shutil.copytree(ROOT, root)
            (root/'unlisted-output.json').write_text('{}')
            (root/'docs').mkdir()
            (root/'docs/private.md').write_text('maintenance only')
            files = self.paths.bundle_files(root)
            expected = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*')
                        if p.is_file() and '__pycache__' not in p.parts}
            self.assertEqual({p.relative_to(root).as_posix() for p in files}, expected)


if __name__ == '__main__':
    unittest.main()
