"""真实 Python 启动及 spawn/fork 重放不把适配器文件导入成第三方包。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

VERIFY = next((Path(__file__).resolve().parents[3] / 'skills').rglob('verify-agent/scripts/verify.py')).parent


class BootstrapTests(unittest.TestCase):
    def test_script_and_children_preserve_dependency_imports(self):
        for mode in ('module', 'cli'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                adapters = root / 'capture/adapters'
                shutil.copytree(VERIFY / 'verification/runtime/capture/adapters', adapters)
                external = root / 'external'; external.mkdir()
                (external / 'coverage.py').write_text('MARKER = "third-party-fixture"\n')
                (external / 'child.py').write_text('''
import coverage
import sys
def observe():
    assert coverage.MARKER == 'third-party-fixture', coverage.__file__
    assert any(type(h).__name__ == 'Hooks' for h in sys.meta_path)
''')
                package = external / 'vllm/entrypoints'
                for path in (package.parent, package, package/'openai', package/'cli'):
                    path.mkdir(parents=True, exist_ok=True); (path/'__init__.py').touch()
                body = '''
import json,multiprocessing,sys
from child import observe

def main():
    observe()
    methods = [m for m in ('spawn','fork') if m in multiprocessing.get_all_start_methods()]
    for method in methods:
        child=multiprocessing.get_context(method).Process(target=observe)
        child.start(); child.join(15)
        assert child.exitcode == 0, (method,child.exitcode)
    print(json.dumps({'methods':methods,'args':sys.argv[1:]}))
'''
                (package/'openai/api_server.py').write_text(body+'\nmain()\n')
                (package/'cli/main.py').write_text(body)
                env = {**os.environ, 'PYTHONPATH': str(external)}
                env.pop('PYTHONSAFEPATH', None)
                result = subprocess.run([sys.executable, '-B', str(adapters/'bootstrap.py'), mode, '--sentinel'],
                                        env=env, cwd=root, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('spawn', json.loads(result.stdout)['methods'])
                self.assertEqual(json.loads(result.stdout)['args'], ['--sentinel'])
