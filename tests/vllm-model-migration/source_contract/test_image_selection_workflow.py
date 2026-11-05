"""客户 task/context 到公开镜像准备回执；只模拟 Quay HTTP、Docker 和无关评测环境。"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


REPOSITORY = 'quay.io/ascend/vllm-ascend'
TAG = REPOSITORY + ':v0.31.0rc10'
INDEX = 'sha256:' + 'a' * 64
DIGEST = 'sha256:' + 'b' * 64
IMAGE_ID = 'sha256:' + 'c' * 64
ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'


def quay_response(request, **kwargs):
    """只响应官方镜像标签接口，禁止测试子进程访问真实网络。"""
    url = urlsplit(getattr(request, 'full_url', request))
    if url.netloc != 'quay.io' or url.path.rstrip('/') != '/api/v1/repository/ascend/vllm-ascend/tag':
        raise OSError('未识别的离线 URL：' + url.geturl())
    names = ['v0.30.0', 'v0.31.0rc10', 'v0.31.0rc2', 'v0.32.0-a3']
    tags = [{'name': name, 'manifest_digest': INDEX if name == 'v0.31.0rc10' else 'sha256:' + 'e' * 64,
             'is_manifest_list': True} for name in names]
    return io.BytesIO(json.dumps({'tags': tags, 'page': 1, 'has_additional': False}).encode())


def docker_cli(args):
    """小型 Docker CLI 替身，按 digest 拉取后才能通过本地 inspect 核验。"""
    root = Path(sys.argv[0]).parent
    pulled = root / 'pulled'
    pinned = REPOSITORY + '@' + DIGEST
    if args[:3] == ['manifest', 'inspect', '--verbose'] and args[3] in (TAG, REPOSITORY + '@' + INDEX):
        result = [{'Descriptor': {'platform': {'os': 'linux', 'architecture': 'arm64'}, 'digest': DIGEST},
                   'SchemaV2Manifest': {'config': {'digest': IMAGE_ID}}}]
    elif args == ['info', '--format', '{{json .}}']:
        result = {'Name': 'fixture', 'ID': 'fixture', 'OSType': 'linux', 'Architecture': 'arm64', 'ServerVersion': '1'}
    elif args == ['image', 'pull', '--platform', 'linux/arm64', pinned]:
        pulled.touch()
        return 0
    elif args[:2] == ['image', 'inspect']:
        if (root / 'index-cache').exists() and args[2] in (TAG, IMAGE_ID):
            digests = [REPOSITORY + '@' + INDEX]
        elif pulled.exists() and args[2] in (pinned, IMAGE_ID):
            digests = [pinned]
        else:
            print('No such image: ' + args[2], file=sys.stderr)
            return 1
        result = [{'Id': IMAGE_ID, 'Os': 'linux', 'Architecture': 'arm64', 'RepoDigests': digests}]
    else:
        raise ValueError('未识别的离线 Docker 命令：' + repr(args))
    print(json.dumps(result))
    return 0


class DefaultImageWorkflowTests(unittest.TestCase):
    def setUp(self):
        paths = patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path])
        paths.start()
        self.addCleanup(paths.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def run_customer(self, reference=None):
        from customer_workflow import runner
        from coordination.runtime import sources
        source, context = self.customer_files(self.root, sources, reference)
        work = self.root / 'work'
        args = SimpleNamespace(work_root=work, run_id='quay-image', context=context, resume=None)
        with patch.dict(os.environ, self.offline_environment(self.root)), \
                patch.object(runner.effects, 'evaluator', return_value=None):
            _, manifest = runner.run(args, source, json.loads(source.read_text()), work / args.run_id / 'customer')
        return manifest['steps']

    def image_result(self):
        from coordination.runtime import sources
        steps = self.run_customer()
        stage = sources.bound(steps['preparation']['receipt'])
        image = stage['tasks']['image']
        self.assertEqual(image['status'], 'completed', image.get('diagnostic'))
        return json.loads(Path(image['manifest']).read_text())

    def test_missing_image_selects_quay_rc_and_pulls_its_platform_digest(self):
        result = self.image_result()
        self.assertEqual(result['inputs']['reference'], TAG)
        self.assertEqual(result['inputs']['expected_digest'], DIGEST)
        self.assertEqual(result['asset']['image_id'], IMAGE_ID)
        commands = json.loads(Path(result['evidence']['commands']).read_text())
        pulls = [row['argv'] for row in commands if row['argv'][:2] == ['image', 'pull']]
        self.assertEqual(pulls, [['image', 'pull', '--platform', 'linux/arm64', REPOSITORY + '@' + DIGEST]])

    def test_matching_tag_cached_with_index_reuses_verified_platform_child(self):
        (self.root / 'index-cache').touch()
        result = self.image_result()
        index = REPOSITORY + '@' + INDEX
        asset = result['asset']
        self.assertEqual(asset['operation'], 'reused')
        self.assertEqual(asset['repo_digests'], [index])
        self.assertEqual(asset['index_manifest'], {'reference': index, 'platform': 'linux/arm64',
                                                   'manifest_digest': DIGEST, 'config_digest': IMAGE_ID})
        commands = json.loads(Path(result['evidence']['commands']).read_text())
        argv = [row['argv'] for row in commands]
        self.assertIn(['manifest', 'inspect', '--verbose', index], argv)
        self.assertFalse(any(command[:2] == ['image', 'pull'] for command in argv))

    def test_stale_agent_reference_blocks_before_image_preparation(self):
        steps = self.run_customer(reference=REPOSITORY + ':v0.30.0')
        preparation = steps['preparation']
        self.assertEqual(preparation['status'], 'blocked')
        self.assertFalse((self.root / 'work/quay-image/vllm-image-prepare').exists())
        self.assertIn('reference', preparation['diagnostic']['message'])

    def offline_environment(self, root):
        # 子 CLI 照常由原入口启动，只替换其外部服务。
        (root / 'sitecustomize.py').write_text(
            'import urllib.request\nfrom test_image_selection_workflow import quay_response\n'
            'urllib.request.urlopen = quay_response\n')
        docker = root / 'docker'
        docker.write_text(f'#!{sys.executable}\nimport sys\nfrom test_image_selection_workflow import docker_cli\n'
                          'sys.exit(docker_cli(sys.argv[1:]))\n')
        docker.chmod(0o755)
        return {'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'PYTHONPATH': os.pathsep.join([str(root), str(Path(__file__).resolve().parent)]),
                'PYTHONDONTWRITEBYTECODE': '1'}

    def customer_files(self, root, sources, reference):
        task = root / 'task.json'
        model = str(root / 'absent-model')
        task.write_text(json.dumps({'model': {'path': model, 'model_name': 'fixture'},
                                    'environment': {'available_npu_devices': [0]}, 'accuracy': {}}))
        hardware = {'name': 'Ascend910B4', 'family': 'Atlas A2'}
        target = {'backend': 'vllm-ascend', 'platform': 'linux/arm64', 'hardware': hardware,
                  'vllm': '0.31.0', 'vllm_ascend': '0.31.0rc10'}
        recipe = {'source_ref': TAG, 'vllm': target['vllm'], 'vllm_ascend': target['vllm_ascend'],
                  'hardware_families': [hardware['family']], 'evidence': ['https://docs.vllm.ai/']}
        identity = {'format': 'vllm-model-file-list-v1', 'sha256': 'd' * 64}
        observations = {'image_request': {'hardware': hardware, 'recipe': recipe}, 'target': target,
                        'version_review': [], 'model_resolution': {
                            'request': {'source': 'local', 'path': model, 'expected_identity': identity},
                            'asset': {'path': model, 'identity': identity}}}
        if reference:
            observations['image_request'].update(source='registry', reference=reference, platform=target['platform'],
                                                 expected_digest=DIGEST)
        context = root / 'context.json'
        context.write_text(json.dumps({'schema_version': '1.0', 'observations': observations, 'evidence': [],
                                       'intent': {'deployment': True, 'performance': False,
                                                  'accuracy': True, 'conflicts': []},
                                       'task_source': sources.ref(task)}))
        return task, context


if __name__ == '__main__':
    unittest.main()
