"""部署地址、容量规划和容器替换必须有明确证据。"""
from pathlib import Path
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))


class VersionIdentityTests(unittest.TestCase):
    def inputs(self, wanted, actual):
        receipt = {'inputs': {'target': {'vllm': wanted, 'vllm_ascend': '0.20.2rc1'}},
                   'details': {'route': 'supported_candidate', 'model': {'config_sha256': 'a' * 64}},
                   'conclusions': {'version_materials': 'pass'}}
        request = {'model': {'config': 'config.json', 'files': {'config.json': 'a' * 64}},
                   'runtime': {'versions': {'vllm': actual, 'vllm-ascend': '0.20.2rc1'}}}
        return receipt, request

    def test_migration_and_deployment_agree_without_rewriting_versions(self):
        from execution.binding import versions
        from verification.inputs import precheck_identity
        cases = [('0.20.2', '0.20.2', True), ('0.20.2', '0.20.2+empty', True),
                 ('0.20.2+empty', '0.20.2+empty', True),
                 ('0.20.2+vendor.1', '0.20.2+vendor.1', True),
                 ('0.20.2rc1', '0.20.2rc1+empty', True),
                 ('0.20.2.dev1', '0.20.2.dev1+empty', True),
                 ('0.20.2.post1', '0.20.2.post1+empty', True),
                 ('0.20.2+empty', '0.20.2+vendor.1', False),
                 ('0.20.2+empty', '0.20.2', False), ('0.20.2', '0.20.3+empty', False),
                 ('0.20.2', '0.20.2rc1+empty', False), ('0.20.2rc1', '0.20.2+empty', False),
                 ('0.20.2', '0.20.2.dev1+empty', False), ('0.20.2', '0.20.2.post1+empty', False)]
        for wanted, actual, accepted in cases:
            receipt, request = self.inputs(wanted, actual)
            original = copy.deepcopy((receipt, request))
            checks = [(versions, (receipt['inputs']['target'], request['runtime']['versions'])),
                      (precheck_identity, (receipt, request))]
            for check, args in checks:
                with self.subTest(wanted=wanted, actual=actual, check=check.__name__):
                    if accepted:
                        check(*args)
                    else:
                        with self.assertRaisesRegex(ValueError, '版本'):
                            check(*args)
                    self.assertEqual((receipt, request), original)

    def test_deployment_rejects_missing_or_invalid_version_identity(self):
        from verification.inputs import precheck_identity
        for wanted, actual in [(None, '0.20.2'), ('0.20.2', None), ('', ''), (123, '0.20.2')]:
            with self.subTest(wanted=wanted, actual=actual):
                receipt, request = self.inputs(wanted, actual)
                with self.assertRaisesRegex(ValueError, '版本'):
                    precheck_identity(receipt, request)
        receipt, request = self.inputs('0.20.2', '0.20.2')
        del request['runtime']['versions']['vllm']
        with self.assertRaisesRegex(ValueError, '版本'):
            precheck_identity(receipt, request)

    def test_runtime_probe_still_requires_full_versions_and_file_identity(self):
        from verification.runtime.inspection import runtime_identity
        _, request = self.inputs('0.20.2', '0.20.2+empty')
        request['runtime'].update(kind='docker', files={'/runtime/registry.py': 'b' * 64}, env={})
        request['model']['runtime_root'] = '/model'
        request['npu'] = {'devices': [0, 1]}
        original = copy.deepcopy(request)
        cases = [('0.20.2+empty', True, True), ('0.20.2+other', True, False),
                 ('0.20.2', True, False), ('0.20.2+empty', False, False)]
        with tempfile.TemporaryDirectory() as directory:
            attempt = Path(directory)
            for actual, file_match, accepted in cases:
                observed = {'versions': {**request['runtime']['versions'], 'vllm': actual},
                            'files': {'/runtime/registry.py': {'match': file_match},
                                      '/model/config.json': {'match': True}}, 'npu_count': 2}
                with self.subTest(actual=actual, file_match=file_match), patch(
                        'verification.runtime.inspection.execute',
                        return_value='VLLM_VERIFY_JSON=' + json.dumps(observed)) as execute:
                    if accepted:
                        self.assertEqual(runtime_identity(request, {}, [], attempt), observed)
                    else:
                        with self.assertRaisesRegex(ValueError, '身份不一致'):
                            runtime_identity(request, {}, [], attempt)
                    expected = json.loads(execute.call_args.kwargs['input_text'])
                    self.assertEqual(expected['versions'], original['runtime']['versions'])
                    self.assertEqual(json.loads((attempt / 'runtime-identity.json').read_text()), observed)
                    self.assertEqual(request, original)


class AddressTests(unittest.TestCase):
    def test_customer_pool_selects_fitting_subset_and_never_outside_devices(self):
        from customer_workflow.compile.deployment import allocation_request
        from verification.customer.capacity import select
        spec = {'weight_bytes': 1000, 'kv_bytes_per_token': 2, 'context_tokens': 100,
                'runtime_bytes_per_device': 100, 'allowed_tp': [1, 2, 4], 'memory_fraction': 0.9}
        devices = [{'id': n, 'free_bytes': 900, 'total_bytes': 1000, 'occupied': False} for n in range(5)]
        task = {'environment': {'available_npu_devices': [1, 2, 3, 4]}}
        request = allocation_request(task, {'devices': devices}, spec)
        self.assertEqual(select(request['spec'], request['devices'], request['requested'], policy=request['policy'])['devices'], [1, 2])
        devices[1]['occupied'] = True
        request = allocation_request(task, {'devices': devices}, spec)
        self.assertEqual(select(request['spec'], request['devices'], request['requested'], policy=request['policy'])['devices'], [2, 3])
        for row in devices[1:]:
            row['occupied'] = True
        request = allocation_request(task, {'devices': devices}, spec)
        self.assertEqual(select(request['spec'], request['devices'], request['requested'], policy=request['policy'])['devices'], [1, 2])
        with self.assertRaisesRegex(ValueError, '观察'):
            allocation_request(task, {'devices': devices[:3]}, spec)

    def test_generated_launch_rejects_allocation_outside_customer_pool(self):
        from customer_workflow.compile.deployment import command
        task = {'model': {'model_name': 'model'}, 'environment': {'available_npu_devices': [1, 2]}}
        allocation = {'devices': [0], 'tensor_parallel_size': 1, 'memory_fraction': .9}
        with self.assertRaisesRegex(ValueError, '设备范围'):
            command(task, {}, {}, allocation, 8000, 9000)

    def test_explicit_docker_context_cannot_hide_a_remote_daemon(self):
        from verification.customer.inspection import local_daemon
        contexts = [{'Endpoints': {'docker': {'Host': 'ssh://remote-host'}}}]
        with patch.dict(os.environ, {'DOCKER_HOST': 'unix:///var/run/docker.sock',
                                     'DOCKER_CONTEXT': 'remote'}, clear=True):
            with patch('verification.customer.inspection.run', return_value=json.dumps(contexts)):
                with self.assertRaisesRegex(ValueError, '本机'):
                    local_daemon(Path('/unused'))

    def test_container_restart_invalidates_delete_consent(self):
        from verification.customer.inspection import replace
        identity = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'started_at': 'old-start'}
        request = {'identity': identity, 'container_name': 'model',
                   'consent': {'approved': True, 'identity': identity}}
        with patch('verification.customer.inspection.local_daemon'), \
                patch('verification.customer.inspection.docker.inspect', return_value={**identity, 'started_at': 'new-start'}), \
                patch('verification.customer.inspection.run') as run:
            with self.assertRaisesRegex(ValueError, '身份已变化'):
                replace(request, Path('/unused'))
            run.assert_not_called()

    def test_numeric_address_shape_and_local_runtime_check(self):
        from service_address import endpoint, assert_local, free_port
        self.assertEqual(endpoint('http://192.0.2.1:8000').hostname, '192.0.2.1')
        for url in ('http://example.com:80', 'http://0.0.0.0:8000', 'http://127.0.0.1:80/v1'):
            with self.assertRaises(ValueError):
                endpoint(url)
        assert_local('127.0.0.1')
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            first = occupied.getsockname()[1]
            if first < 65535:
                self.assertGreater(free_port('127.0.0.1', first), first)

    def test_resource_selection_respects_explicit_devices_and_tp(self):
        from verification.customer.capacity import select
        spec = {'weight_bytes': 1000, 'kv_bytes_per_token': 2, 'context_tokens': 100,
                'runtime_bytes_per_device': 100, 'allowed_tp': [1, 2], 'memory_fraction': 0.9}
        devices = [{'id': 0, 'free_bytes': 900, 'total_bytes': 1000, 'occupied': False},
                   {'id': 1, 'free_bytes': 900, 'total_bytes': 1000, 'occupied': False}]
        result = select(spec, devices, -1)
        self.assertEqual(result['devices'], [0, 1])
        self.assertEqual(result['tensor_parallel_size'], 2)
        with self.assertRaises(ValueError):
            select(spec, devices, [0])
        devices[1]['occupied'] = True
        with self.assertRaises(ValueError):
            select(spec, devices, -1)


class ContainerReplacementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.identity = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'started_at': 'old-start'}
        self.raw = {'Id': self.identity['container_id'], 'Image': self.identity['image_id'], 'Name': '/model',
                    'State': {'Status': 'exited', 'Running': False, 'StartedAt': 'old-start', 'Pid': 0},
                    'Config': {'Labels': {}}, 'HostConfig': {'NetworkMode': 'host'}}
        self.before = {**self.identity, 'running': False, 'raw': self.raw}
        self.request = {'operation': 'replace_container', 'container_name': 'model', 'identity': self.identity}

    def test_observation_includes_fresh_exit_state(self):
        from verification.customer.inspection import existing_container
        with patch('verification.customer.inspection.run', return_value=self.identity['container_id']), \
                patch('verification.customer.inspection.docker.inspect', return_value=self.before):
            self.assertEqual(existing_container('model', self.root),
                             {**self.identity, 'status': 'exited', 'running': False})

    def test_exited_replacement_needs_no_consent_and_never_forces(self):
        from verification.customer.inspection import replace
        with patch('verification.customer.inspection.local_daemon'), \
                patch('verification.customer.inspection.docker.inspect', return_value=self.before) as inspect, \
                patch('verification.customer.inspection.run', side_effect=[self.identity['container_id'], '']) as run:
            result = replace(self.request, self.root)
        inspect.assert_called_once_with(self.identity['container_id'], self.root, 'replacement-before')
        self.assertEqual(run.call_args_list[0].args[0], ['docker', 'rm', self.identity['container_id']])
        self.assertEqual(result, {'removed': self.identity, 'container_name': 'model'})

    def test_identity_name_and_exit_state_are_rechecked_before_deletion(self):
        from verification.customer.inspection import replace
        cases = [dict(container_id='c' * 64), dict(image_id='sha256:' + 'c' * 64),
                 dict(started_at='new-start'), dict(running=True)]
        cases.append({'raw': {**self.raw, 'Name': '/other'}})
        cases.extend({'raw': {**self.raw, 'State': {**self.raw['State'], 'Status': status}}}
                     for status in ('running', 'paused', 'created', 'dead', 'restarting', 'removing', None))
        for changes in cases:
            with self.subTest(changes=changes), patch('verification.customer.inspection.local_daemon'), \
                    patch('verification.customer.inspection.docker.inspect', return_value={**self.before, **changes}), \
                    patch('verification.customer.inspection.run') as run:
                with self.assertRaisesRegex(ValueError, '身份|名称|退出'):
                    replace(self.request, self.root)
                run.assert_not_called()

    def test_invalid_identity_is_rejected_before_docker(self):
        from verification.customer.inspection import replace
        for identity in ({}, {**self.identity, 'container_id': 'abc'}, {**self.identity, 'extra': 'field'}):
            with self.subTest(identity=identity), patch('verification.customer.inspection.local_daemon') as daemon:
                with self.assertRaises(ValueError):
                    replace({**self.request, 'identity': identity}, self.root)
                daemon.assert_not_called()

    def test_failed_removal_never_retries_with_force(self):
        from verification.customer.inspection import replace
        with patch('verification.customer.inspection.local_daemon'), \
                patch('verification.customer.inspection.docker.inspect', return_value=self.before), \
                patch('verification.customer.inspection.run', side_effect=RuntimeError('container is running')) as run:
            with self.assertRaisesRegex(RuntimeError, 'running'):
                replace(self.request, self.root)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['docker', 'rm', self.identity['container_id']])

    def test_removed_container_must_disappear(self):
        from verification.customer.inspection import replace
        with patch('verification.customer.inspection.local_daemon'), \
                patch('verification.customer.inspection.docker.inspect', return_value=self.before), \
                patch('verification.customer.inspection.run', return_value=self.identity['container_id']) as run:
            with self.assertRaisesRegex(ValueError, '仍可观察'):
                replace(self.request, self.root)
            self.assertEqual(run.call_count, 2)

    def test_explicit_consent_retains_legacy_path_and_rejects_wrong_identity(self):
        from verification.customer.inspection import replace
        consent = {'approved': True, 'identity': self.identity}
        running = {**self.before, 'running': True, 'raw': {**self.raw, 'State': {'Status': 'running'}}}
        with patch('verification.customer.inspection.local_daemon'), \
                patch('verification.customer.inspection.docker.inspect', return_value=running), \
                patch('verification.customer.inspection.run', side_effect=[self.identity['container_id'], '']) as run:
            replace({**self.request, 'consent': consent}, self.root)
            self.assertEqual(run.call_args_list[0].args[0], ['docker', 'rm', '--force', self.identity['container_id']])
        for invalid in ({'approved': False, 'identity': self.identity}, {'approved': True, 'identity': {}}, []):
            with self.subTest(invalid=invalid), patch('verification.customer.inspection.local_daemon'), \
                    patch('verification.customer.inspection.docker.inspect', return_value=running), \
                    patch('verification.customer.inspection.run') as run:
                with self.assertRaises(ValueError):
                    replace({**self.request, 'consent': invalid}, self.root)
                run.assert_not_called()

    def fake_environment(self):
        """只替代Docker daemon边界，实际公开CLI和生命周期命令仍执行。"""
        state = self.root / 'docker-state.json'
        trace = self.root / 'docker-trace.json'
        state.write_text(json.dumps(self.raw))
        binary = self.root / 'docker'
        binary.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['TEST_DOCKER_STATE']);t=Path(os.environ['TEST_DOCKER_TRACE'])
args=sys.argv[1:];history=json.loads(t.read_text()) if t.exists() else []
history.append(args);t.write_text(json.dumps(history));value=json.loads(p.read_text())
if args[:2]==['context','inspect']:
 print(json.dumps([{'Endpoints':{'docker':{'Host':'unix:///fixture.sock'}}}]))
elif args[0]=='inspect':
 if value is None or args[1]!=value['Id']:sys.exit('No such container')
 print(json.dumps([value]))
elif args[0]=='ps':
 if value is not None:print(value['Id'])
elif args[0]=='rm':
 if value is None or args[-1]!=value['Id']:sys.exit('No such container')
 if os.environ.get('TEST_DOCKER_RACE')=='restart':
  value['State'].update(Status='running',Running=True,StartedAt='restarted')
  p.write_text(json.dumps(value))
 if value['State']['Running'] and '--force' not in args:sys.exit('container is running')
 p.write_text('null');print(args[-1])
elif args[0]=='create':
 if value is not None:sys.exit('container name already in use')
 label=args[args.index('--label')+1].split('=',1)
 value={'Id':'c'*64,'Image':args[args.index('--entrypoint')+2],
        'Name':'/'+args[args.index('--name')+1],
        'State':{'Status':'created','Running':False,'StartedAt':'not-started','Pid':0},
        'Config':{'Labels':dict([label])},'HostConfig':{'NetworkMode':'host'}}
 p.write_text(json.dumps(value));print(value['Id'])
elif args[0]=='start':
 if value is None or args[1]!=value['Id']:sys.exit('No such container')
 value['State'].update(Status='running',Running=True,StartedAt='new-start',Pid=123)
 p.write_text(json.dumps(value));print(value['Id'])
else:sys.exit('unexpected Docker operation')
''')
        binary.chmod(0o755)
        return {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ.get('PATH', ''),
                'TEST_DOCKER_STATE': str(state), 'TEST_DOCKER_TRACE': str(trace),
                'DOCKER_CONTEXT': '', 'DOCKER_HOST': 'unix:///fixture.sock'}

    def invoke_replacement(self, env, plan=False):
        request = self.root / 'replace.json'
        request.write_text(json.dumps(self.request))
        entry = ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts/customer.py'
        argv = [sys.executable, '-B', str(entry), '--request', str(request), '--work-root',
                str(self.root / 'runs'), '--run-id', 'replacement']
        if plan:
            argv.append('--plan')
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
        return result, json.loads(result.stdout)

    def test_public_cli_deletes_exited_identity_then_allows_same_name_rebuild(self):
        from verification.runtime.docker import create
        env = self.fake_environment()
        preview, report = self.invoke_replacement(env, plan=True)
        self.assertEqual((preview.returncode, report['status']), (0, 'not_run'))
        self.assertFalse(Path(env['TEST_DOCKER_TRACE']).exists())
        result, report = self.invoke_replacement(env)
        self.assertEqual((result.returncode, report['status']), (0, 'completed'), result.stdout + result.stderr)
        self.assertEqual(report['result']['removed'], self.identity)
        self.assertIsNone(json.loads(Path(env['TEST_DOCKER_STATE']).read_text()))
        self.assertEqual(json.loads(Path(report['manifest']).read_text())['status'], 'completed')
        request = {'service': {'docker': {'name': 'model', 'image_id': self.identity['image_id'],
                   'shm_size': '1g', 'memory': '1g', 'cpus': 1, 'devices': ['/dev/davinci0'], 'mounts': []}},
                   'npu': {'devices': [0]}, 'runtime': {'python': 'python3', 'env': {}}}
        attempt = self.root / 'rebuild'
        attempt.mkdir()
        with patch.dict(os.environ, env, clear=True):
            resource = create(request, attempt, 'new-owner')
        self.assertEqual(resource['identity']['container_id'], 'c' * 64)
        rebuilt = json.loads(Path(env['TEST_DOCKER_STATE']).read_text())
        self.assertEqual(rebuilt['Name'], '/model')
        self.assertTrue(rebuilt['State']['Running'])
        trace = json.loads(Path(env['TEST_DOCKER_TRACE']).read_text())
        self.assertEqual([row for row in trace if row[0] == 'rm'], [['rm', self.identity['container_id']]])

    def test_public_cli_refuses_container_restarted_after_final_inspect(self):
        env = self.fake_environment()
        env['TEST_DOCKER_RACE'] = 'restart'
        result, report = self.invoke_replacement(env)
        self.assertEqual((result.returncode, report['status']), (3, 'blocked'), result.stdout + result.stderr)
        self.assertTrue(json.loads(Path(env['TEST_DOCKER_STATE']).read_text())['State']['Running'])
        trace = json.loads(Path(env['TEST_DOCKER_TRACE']).read_text())
        self.assertEqual([row for row in trace if row[0] == 'rm'], [['rm', self.identity['container_id']]])

    def test_public_cli_does_not_delete_a_new_container_with_the_same_name(self):
        env = self.fake_environment()
        replacement = copy.deepcopy(self.raw)
        replacement['Id'] = 'd' * 64
        Path(env['TEST_DOCKER_STATE']).write_text(json.dumps(replacement))
        result, report = self.invoke_replacement(env)
        self.assertEqual((result.returncode, report['status']), (3, 'blocked'))
        self.assertEqual(json.loads(Path(env['TEST_DOCKER_STATE']).read_text()), replacement)
        trace = json.loads(Path(env['TEST_DOCKER_TRACE']).read_text())
        self.assertFalse(any(row[0] == 'rm' for row in trace))


class ImagePackageProbeTests(unittest.TestCase):
    def fixture(self, layout='wheel'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        site = root / 'installed'
        site.mkdir()
        paths = [str(site)]
        records = {}
        for distribution, module in (('vllm', 'vllm'), ('vllm-ascend', 'vllm_ascend')):
            project = root / ('source 空格 ' + module)
            project.mkdir()
            base = site if layout == 'wheel' else project / 'src' if layout == 'src' else project
            package = base / module
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('raise RuntimeError("target package must not be imported")\n')
            paths.append(str(base))
            metadata = site / (module + '-1.2.3.dist-info')
            metadata.mkdir()
            (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + distribution + '\nVersion: 1.2.3\n')
            entry = module + '/__init__.py,,\n' if layout == 'wheel' else '__editable__.pth,,\n'
            (metadata / 'RECORD').write_text(entry)
            if layout != 'wheel':
                (metadata / 'direct_url.json').write_text(json.dumps({'url': project.as_uri(), 'dir_info': {'editable': True}}))
            records[module] = {'project': project, 'package': package, 'metadata': metadata}
        return root, paths, records

    def probe(self, paths):
        from verification.customer.image_probe import PROBE
        script = 'import sys\nsys.path[:0] = ' + repr(paths) + '\n' + PROBE
        return subprocess.run([sys.executable, '-I', '-B', '-c', script], capture_output=True, text=True, timeout=15)

    def test_wheel_and_editable_layouts_resolve_without_importing_packages(self):
        for layout in ('wheel', 'flat', 'src'):
            with self.subTest(layout=layout):
                _, paths, records = self.fixture(layout)
                result = self.probe(paths)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value['versions'], {'vllm': '1.2.3', 'vllm-ascend': '1.2.3'})
                for module, record in records.items():
                    self.assertEqual(value['sources'][module]['path'], str(record['package']))

    def test_image_probe_keeps_the_full_local_version(self):
        _, paths, records = self.fixture()
        metadata = records['vllm']['metadata'] / 'METADATA'
        metadata.write_text(metadata.read_text().replace('Version: 1.2.3', 'Version: 0.20.2+empty'))
        result = self.probe(paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['versions']['vllm'], '0.20.2+empty')
        self.assertEqual(value['sources']['vllm']['version'], '0.20.2+empty')

    def test_shadowed_packages_cannot_replace_installed_sources(self):
        for layout in ('wheel', 'src'):
            with self.subTest(layout=layout):
                root, paths, _ = self.fixture(layout)
                shadow = root / 'shadow' / 'vllm'
                shadow.mkdir(parents=True)
                (shadow / '__init__.py').write_text('raise RuntimeError("do not import")\n')
                result = self.probe([str(shadow.parent), *paths])
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, '安装记录|来源目录')

    def test_editable_requires_valid_local_origin_and_existing_package(self):
        for problem in ('missing', 'broken', 'not-editable', 'remote', 'outside', 'missing-root', 'missing-package'):
            with self.subTest(problem=problem):
                root, paths, records = self.fixture('src')
                record = records['vllm']
                metadata = record['metadata'] / 'direct_url.json'
                origin = {'url': record['project'].as_uri(), 'dir_info': {'editable': True}}
                if problem == 'missing':
                    metadata.unlink()
                elif problem == 'broken':
                    metadata.write_text('{')
                elif problem == 'missing-package':
                    (record['package'] / '__init__.py').unlink()
                else:
                    if problem == 'not-editable':
                        origin['dir_info']['editable'] = False
                    elif problem == 'remote':
                        origin['url'] = 'https://example.invalid/source'
                    elif problem == 'outside':
                        origin['url'] = records['vllm_ascend']['project'].as_uri()
                    elif problem == 'missing-root':
                        origin['url'] = (root / 'absent').as_uri()
                    metadata.write_text(json.dumps(origin))
                result = self.probe(paths)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, 'editable|来源|普通 Python 包')

    def test_duplicate_package_records_are_not_an_editable_fallback(self):
        _, paths, records = self.fixture()
        (records['vllm']['metadata'] / 'RECORD').write_text('vllm/__init__.py,,\nvllm/__init__.py,,\n')
        result = self.probe(paths)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('多个候选', result.stderr)

    def test_zip_packages_are_explicitly_unsupported(self):
        from zipfile import ZipFile
        root, paths, _ = self.fixture('src')
        archive = root / 'packages.zip'
        with ZipFile(archive, 'w') as bundle:
            bundle.writestr('vllm/__init__.py', 'raise RuntimeError("do not import")\n')
        result = self.probe([str(archive), *paths])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('普通 Python 包', result.stderr)


if __name__ == '__main__':
    unittest.main()
