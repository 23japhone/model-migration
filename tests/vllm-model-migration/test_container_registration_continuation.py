"""合成证据的注册→续接→三 JSON 导出回归；仅模拟 Docker，不运行模型或评分。"""
import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]/'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'skills/vllm-migration-execute/scripts'))

from coordination.runtime import bindings, continuation, evidence, preflight, sources
from execution import receipts as execution_receipts, transport
from tuning_handoff import cli as export_cli
from tuning_handoff.request import FILES


class ContainerRegistrationContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='容器注册 续接 ')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.attempt = self.base/'migration'
        self.attempt.mkdir()
        self.run_id = 'synthetic-registration'
        self.marker = self.write('inputs/synthetic-only.json', {'synthetic': True})
        self.container = {'container_id': 'a'*64, 'image_id': 'sha256:'+'b'*64,
                          'started_at': 'synthetic-start', 'python': '/container/bin/python3'}
        self.make_registration()
        self.make_migration()

    def write(self, relative, value):
        path = self.base/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        return path

    def task(self, skill, inputs, conclusions, **extra):
        return {'schema_version': '1.0', 'task_id': skill, 'run_id': self.run_id,
                'status': 'completed', 'inputs': inputs, 'conclusions': conclusions,
                'evidence': {'synthetic': str(self.marker)}, 'uncovered': ['仅为合成回归证据'], **extra}

    def row(self, path, value, **extra):
        return {'status': value['status'], 'receipt': sources.ref(path),
                'conclusions': value['conclusions'], **extra}

    def phase(self, name, value, kind):
        path = self.base/name
        parent = path.parent
        ledger = self.write(parent.relative_to(self.base)/'task-ledger.json', value['tasks'])
        handoff = {'schema_version': '1.0', 'conclusions': value['conclusions']}
        value['evidence'] = {'manifest': str(path), 'ledger': str(ledger)}
        if kind == 'preparation':
            handoff.update(run_id=self.run_id, phase_manifest=str(path), uncovered=value['uncovered'],
                           target=value['inputs'].get('target'), assets={
                               key: {field: row.get(field) for field in (
                                   'required', 'status', 'manifest', 'manifest_sha256', 'asset', 'conclusions')}
                               for key, row in value['tasks'].items()})
        else:
            handoff.update({key: value.get(key) for key in (
                ('tasks', 'route', 'deployment', 'uncovered') if kind == 'migration' else
                ('task_id', 'run_id', 'status', 'tasks', 'results', 'resources', 'uncovered'))})
            handoff['stage' if kind == 'migration' else 'ledger'] = sources.ref(ledger)
        names = {'preparation': 'prepare-handoff.json', 'migration': 'migration-handoff.json',
                 'precision': 'precision-stage-handoff.json'}
        handoff_path = self.write(parent.relative_to(self.base)/names[kind], handoff)
        value['evidence']['handoff'] = str(handoff_path)
        if kind == 'precision':
            value['handoff'] = sources.ref(handoff_path)
        return self.write(name, value)

    def make_registration(self):
        self.declared = json.loads((ROOT/'skills/vllm-migration-execute/skills/vllm-model-register/'
                                    'assets/source.request.example.json').read_text())
        self.declared['model']['config'] = 'model/config.json'
        self.normalized = copy.deepcopy(self.declared)
        self.normalized['model']['config'] = '/container/model/config.json'
        self.register_source = self.write('inputs/register.json', self.declared)
        with patch.object(transport, 'call', side_effect=self.docker_call):
            code, text, normalized = transport.execute(
                self.container, self.declared, self.attempt, self.run_id, 10)
        self.registered, self.registration_ref = execution_receipts.child(
            text, code, 'register', normalized, self.attempt/'children/register', self.run_id)

    def docker_call(self, argv, timeout, attempt, label):
        """只代替 Docker 外部效果，传输模块的文件枚举、冻结和来源映射照常执行。"""
        if argv[1] == 'inspect':
            return 0, json.dumps([{'Id': self.container['container_id'], 'Image': self.container['image_id'],
                                  'State': {'StartedAt': self.container['started_at'], 'Running': True}}])
        if argv[1] == 'cp':
            if label == 'register-evidence-copy':
                relative = Path(self.run_id)/'vllm-model-register/child/registration-manifest.json'
                receipt = self.task('vllm-model-register', self.normalized, {'registration': 'pass'})
                self.write(Path(argv[-1]).relative_to(self.base)/relative, receipt)
            return 0, ''
        self.assertEqual(argv[1], 'exec')
        if '--plan' in argv:
            result = {'status': 'not_run', 'request': self.normalized}
        else:
            remote = argv[argv.index('--work-root')+1]
            result = {'manifest': remote+'/'+self.run_id+'/vllm-model-register/child/registration-manifest.json'}
        self.write(attempt.relative_to(self.base)/(label+'.stdout.log'), result)
        return 0, json.dumps(result)

    def make_preparation(self):
        tasks = {}
        for name in ('model', 'image'):
            value = self.task('vllm-'+name+'-prepare', {}, {'assets': 'pass'}, asset={'synthetic': name})
            path = self.write('preparation/'+name+'.json', value)
            tasks[name] = self.row(path, value, required=True, asset=value['asset'])
        inputs = {'purpose': 'deployment', 'tasks': {name: {'required': True} for name in tasks}}
        value = self.task('vllm-migration-prepare', inputs,
                          {'preparation': 'pass', 'assets': 'pass', 'version_materials': 'pass'}, tasks=tasks)
        return sources.ref(self.phase('preparation/prepare-manifest.json', value, 'preparation'))

    def make_verification(self, handoffs):
        self.model_root = self.base/'model'
        tokenizer = self.write('model/tokenizer_config.json', {'synthetic': True})
        self.cann = self.base/'synthetic-cann'
        self.cann.mkdir()
        self.endpoint = 'http://127.0.0.1:18000'
        planned = {'id': 'text', 'kind': 'generate', 'required': True}
        inputs = {'model': {'name': 'synthetic', 'root': str(self.model_root),
                            'runtime_root': str(self.model_root),
                            'files': {'tokenizer_config.json': sources.ref(tokenizer)['sha256']}},
                  'runtime': {'kind': 'docker', 'env': {'ASCEND_HOME_PATH': str(self.cann)}},
                  'npu': {'devices': [4, 5]},
                  'service': {'ownership': 'owned', 'endpoint': self.endpoint, 'command': [
                      'vllm', 'serve', str(self.model_root), '--served-model-name', 'synthetic',
                      '--host', '127.0.0.1', '--port', '18000']}, 'cases': [planned], 'handoffs': handoffs}
        self.verify_source = self.write('inputs/verify.json', {k: v for k, v in inputs.items() if k != 'handoffs'})
        response = self.write('verify/response.json', {'synthetic': 'response'})
        raw = self.write('verify/http.json', {'synthetic': 'HTTP bytes'})
        cases = [{**planned, 'response': {'path': str(response), 'sha256': sources.ref(response)['sha256'],
                                         'raw_path': str(raw), 'raw_sha256': sources.ref(raw)['sha256']}}]
        case_path = self.write('verify/cases.json', cases)
        self.handoff = {k: inputs[k] for k in ('model', 'runtime')}
        self.handoff.update(endpoint=self.endpoint, final_service={'state': 'retained'})
        self.handoff['resource'] = {'kind': 'docker', 'ownership': 'owned', 'nonce': 'synthetic-owner',
            'identity': {k: self.container[k] for k in ('container_id', 'image_id', 'started_at')}}
        from devices.fixtures import attach
        self.effective_runtime = attach(self.base / 'synthetic-devices', inputs, self.handoff)
        handoff_path = self.write('verify/deployment.json', self.handoff)
        value = self.task('verify-agent', inputs, {key: 'pass' for key in execution_receipts.VERIFY_KEYS},
                          operation='verify', success=True, detailed_report={
                              'cases': cases, 'handoff_sha256': sources.ref(handoff_path)['sha256']})
        value['evidence'] = {'cases': str(case_path), 'handoff': str(handoff_path)}
        self.verify_path = self.write('verify/verify-manifest.json', value)
        return value, sources.ref(handoff_path)

    def make_migration(self):
        preparation = self.make_preparation()
        precheck_inputs = {'model': {'path': str(self.base/'model')}, 'sources': {}}
        precheck_source = self.write('inputs/precheck.json', precheck_inputs)
        precheck_value = self.task('vllm-model-precheck', {**precheck_inputs, 'features': ['eager']},
                                   {'routing': 'pass'}, details={'route': 'registration_required'})
        precheck_path = self.write('precheck/result.json', precheck_value)
        handoffs = {'prepare': preparation, 'precheck': sources.ref(precheck_path),
                    'registration': self.registration_ref}
        verify_value, deployment = self.make_verification(handoffs)
        inputs = {'schema_version': '1.0', 'preparation': preparation,
                  'precheck': sources.ref(precheck_source), 'verify': sources.ref(self.verify_source),
                  'register': sources.ref(self.register_source), 'register_container': self.container}
        tasks = {'precheck': self.row(precheck_path, precheck_value), 'adapt': {'status': 'skipped'},
                 'register': self.row(Path(self.registration_ref['path']), self.registered),
                 'verify': self.row(self.verify_path, verify_value)}
        self.migration = self.task('vllm-migration-execute', inputs,
                                   {key: 'pass' for key in ('preparation', 'migration', 'functional_validation')},
                                   tasks=tasks, route='registration_required', deployment=deployment)
        self.migration_path = self.phase('migration/migration-manifest.json', self.migration, 'migration')

    def make_dataset(self):
        path = self.base/'data/samples.jsonl'
        path.parent.mkdir()
        path.write_text('{"question":"synthetic","answer":"test"}\n', encoding='utf-8')
        files = [{'path': path.name, 'size': path.stat().st_size, 'sha256': sources.ref(path)['sha256']}]
        records = [{'file': path.name, 'row_index': 0}]
        from tuning_handoff.baseline_data import digest
        asset = {'path': str(path.parent), 'files': [{**files[0], 'rows': 1}],
                 'identity': {'format': 'vllm-dataset-file-list-v1', 'sha256': digest(files)},
                 'selection': {'count': 1, 'records': records, 'sha256': digest(records), 'split': 'test'}}
        receipt = self.task('vllm-dataset-prepare', {}, {'assets': 'pass'}, asset=asset)
        return sources.ref(self.write('data/receipt.json', receipt)), asset, path

    def make_baseline(self, request_path):
        inputs = sources.read(request_path)
        dataset, asset, data_path = self.make_dataset()
        inputs['dataset'] = dataset
        config = {'model': 'synthetic', 'api_url': self.endpoint+'/v1', 'eval_type': 'openai_api',
                  'eval_backend': 'Native', 'datasets': ['synthetic'], 'eval_batch_size': 1, 'limit': None,
                  'generation_config': {}, 'model_args': {}, 'judge': {},
                  'dataset_args': {'synthetic': {'local_path': str(data_path), 'subset_list': ['test'],
                                                  'eval_split': 'test'}}}
        config_path = self.write('baseline/frozen-config.json', config)
        inputs['schema_version'] = '1.0'
        inputs['evaluator'] = {'python': sys.executable, 'backend': 'evalscope', 'version': '1.0.0',
                               'config': sources.ref(config_path), 'timeout_seconds': 10}
        inputs['tasks'] = [{'dataset': 'synthetic', 'model_abbr': 'synthetic', 'benchmark': 'synthetic',
                            'subset': 'test', 'sample_ids': [0], 'metrics': {'accuracy': {
                                'selector': {'name': 'accuracy', 'aggregation': 'mean', 'dimensions': {}},
                                'unit': 'ratio', 'direction': 'higher'}}}]
        self.write(request_path.relative_to(self.base), inputs)
        details = {'conditions': {'evaluator_backend': 'evalscope', 'dataset': asset['identity'],
                                  'selection': asset['selection']['sha256'], 'model': {}},
                   'tasks': [{**inputs['tasks'][0], 'config': str(config_path)}],
                   'files': {str(config_path): sources.ref(config_path)['sha256']},
                   'measurements': [{'dataset': 'synthetic', 'valid': True, 'metrics': {'accuracy': 1.0}}]}
        value = self.task('verify-agent-plus', inputs, {'evaluation_validity': 'pass',
                          'comparability': 'not_evaluated', 'precision_target': 'not_evaluated'},
                          operation='run', success=True, detailed_report=details)
        path = self.write('baseline/result.json', value)
        return value, sources.ref(path), data_path

    def make_entry(self):
        migration = {'result': sources.ref(self.migration_path)}
        evaluation = self.write('inputs/evaluation.json', {})
        stage_inputs = {'schema_version': '1.0', 'evaluations': [{'id': 'baseline', 'request': sources.ref(evaluation)}]}
        stage_source = self.write('inputs/precision.json', stage_inputs)
        request = {'schema_version': '1.0', 'scope': 'deployment_precision', 'stages': {
            'migration': migration, 'precision': {'request': sources.ref(stage_source),
                                                 'bindings': {'baseline': {'deployment': 'migration'}}}}}
        request_path = self.write('inputs/entry.json', request)
        parent = self.base/'entry'
        attempt, manifest = evidence.begin(request, parent, self.run_id, 'run', sources.ref(request_path))
        reused = preflight.stage_source('migration', migration)
        manifest['stages']['migration'] = {**self.row(self.migration_path, reused), 'source': migration,
                                           'input_snapshot': migration['result'], 'mode': 'reuse', 'exit_code': 0}
        manifest['stages']['precision'] = {'status': 'not_run'}
        frozen = bindings.precision(request, stage_inputs, manifest, attempt)
        baseline, receipt, data_path = self.make_baseline(Path(frozen['evaluations'][0]['request']['path']))
        frozen['evaluations'][0]['request'] = sources.ref(Path(frozen['evaluations'][0]['request']['path']))
        results = {'baseline': {'receipt': receipt, 'conclusions': baseline['conclusions'],
                               'measurements': baseline['detailed_report']['measurements'],
                               'conditions': baseline['detailed_report']['conditions'], 'uncovered': baseline['uncovered']}}
        conclusions = {'precision_testing': 'pass', **baseline['conclusions']}
        phase = self.task('vllm-precision-align', frozen, conclusions, operation='run', success=True,
                          tasks={'datasets': {}, 'deployments': {}, 'evaluations': {
                              'baseline': self.row(Path(receipt['path']), baseline)}}, results=results, resources=[])
        phase_path = self.phase('precision/result.json', phase, 'precision')
        snapshot = self.write('inputs/precision-frozen.json', frozen)
        manifest['stages']['precision'] = {**self.row(phase_path, phase), 'source': request['stages']['precision'],
                                          'input_snapshot': sources.ref(snapshot), 'exit_code': 0}
        manifest.update(status='completed', success=True, conclusions=conclusions, results=results, next_action='complete')
        evidence.publish(attempt, manifest)
        return sources.ref(Path(manifest['evidence']['manifest'])), data_path

    def export_request(self):
        entry, data_path = self.make_entry()
        service = {'base_url': self.endpoint, 'model': 'synthetic', 'request_timeout_seconds': 10}
        benchmark = {'service': service, 'tokenizer_path': str(self.model_root), 'output_dir': '../outputs',
                     'benchmark': {**dict.fromkeys(('requests_per_run', 'input_tokens', 'output_tokens', 'timeout_seconds'), 1),
                                   'concurrency': [1], 'warmup_requests': 0, 'seed': 0,
                                   'enable_thinking': False, 'ignore_eos': False},
                     'runtime_metrics': {'enabled': False, 'interval_sec': 1, 'timeout_sec': 1}}
        accuracy = {'service': service, 'output_dir': '../outputs', 'evaluation': {
            'model_path': 'synthetic', 'api_key': 'EMPTY', 'timeout_seconds': 10, 'datasets': [{
                'name': 'synthetic', 'evaluator': 'evalscope', 'dataset': str(data_path), 'subset_list': ['test'],
                'dataset_args': {'eval_split': 'test'}, 'limit': None, 'eval_batch_size': 1,
                'extra_args': ['--generation-config', '{}', '--model-args', '{}', '--judge', '{}', '--eval-backend', 'Native']}]}}
        deployment = {'cann_path': str(self.cann), 'env_variables': self.effective_runtime['env'],
                      'log_file': '../outputs/server.log', 'server': {'model_path': str(self.model_root),
                      'served_model_name': 'synthetic', 'host': '127.0.0.1', 'port': 18000}}
        configs = {'benchmark_plan': benchmark, 'accuracy_plan': accuracy, 'deployment': deployment}
        request = {'schema_version': '1.0', 'entry_manifest': entry, **{
            key: sources.ref(self.write('candidates/'+key+'.json', value)) for key, value in configs.items()}}
        request_path = self.write('inputs/export.json', request)
        return request_path, configs

    def export(self, request_path):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = export_cli.main(['--request', str(request_path), '--work-root', str(self.base/'exports'),
                                    '--run-id', 'synthetic-export'])
        return code, json.loads(output.getvalue())

    def test_registration_result_reuse_and_three_json_export(self):
        previous = {p: p.read_bytes() for p in self.attempt.rglob('*') if p.is_file()}
        request_path, configs = self.export_request()
        code, result = self.export(request_path)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(set(result['deliverables']), set(FILES))
        for key, reference in result['deliverables'].items():
            self.assertEqual(sources.bound(reference), configs[key])
        published = Path(result['manifest']).parent/'deliverables'
        self.assertEqual({p.relative_to(published).as_posix() for p in published.rglob('*') if p.is_file()}, set(FILES.values()))
        self.assertEqual(previous, {p: p.read_bytes() for p in previous})
        self.assertFalse((self.attempt/'registration-resources/request.json').exists())

    def test_export_rejects_changed_request_origin_and_public_plan(self):
        request_path, _ = self.export_request()
        self.write('migration/registration-resources/request.json', self.declared)
        cases = [('register.request.json', {'changed': True}, '复制前请求与声明不一致'),
                 ('register-origin.json', {'container': {}}, '来源映射不一致'),
                 ('register-plan.stdout.log', {'status': 'completed'}, '公开计划无效')]
        for name, replacement, reason in cases:
            with self.subTest(name=name):
                path = self.attempt/name
                original = path.read_bytes()
                try:
                    self.write(Path('migration')/name, replacement)
                    code, result = self.export(request_path)
                    self.assertEqual(code, 3, result)
                    self.assertEqual(result['status'], 'blocked')
                    self.assertIn(reason, result['reason'])
                    self.assertFalse((self.base/'exports').exists())
                finally:
                    path.write_bytes(original)

    def test_saved_request_mismatch_rejects_even_if_old_location_matches(self):
        self.write('migration/register.request.json', {'changed': True})
        self.write('migration/registration-resources/request.json', self.declared)
        with self.assertRaisesRegex(ValueError, '复制前请求与声明不一致'):
            continuation.reusable(self.migration)

    def test_missing_saved_request_does_not_fall_back_to_old_location(self):
        self.write('migration/registration-resources/request.json', self.declared)
        (self.attempt/'register.request.json').unlink()
        with self.assertRaises(FileNotFoundError):
            continuation.reusable(self.migration)

    def test_origin_identity_and_remote_receipt_paths_remain_bound(self):
        path = self.attempt/'register-origin.json'
        original = sources.read(path)
        for key, value in (('container', {}), ('local_receipt', {}), ('remote_manifest', '/other/result.json')):
            with self.subTest(key=key):
                self.write('migration/register-origin.json', {**original, key: value})
                with self.assertRaises(ValueError):
                    continuation.reusable(self.migration)
        self.write('migration/register-origin.json', original)

    def test_public_plan_status_and_normalized_inputs_remain_bound(self):
        cases = [({'status': 'completed', 'request': self.normalized}, '公开计划无效'),
                 ({'status': 'not_run'}, '公开计划无效'),
                 ({'status': 'not_run', 'request': {'changed': True}}, '原始注册输入与声明请求不一致')]
        for plan, reason in cases:
            with self.subTest(reason=reason):
                self.write('migration/register-plan.stdout.log', plan)
                with self.assertRaisesRegex(ValueError, reason):
                    continuation.reusable(self.migration)

    def test_local_receipt_cannot_leave_original_copy_location(self):
        original = copy.deepcopy(self.migration)
        copied = self.write('other/registration.json', self.registered)
        original['tasks']['register']['receipt'] = sources.ref(copied)
        origin = sources.read(self.attempt/'register-origin.json')
        origin['local_receipt'] = sources.ref(copied)
        self.write('migration/register-origin.json', origin)
        with self.assertRaisesRegex(ValueError, '复制回执位置不一致'):
            continuation.container_registration(original, self.attempt, self.declared)

    def test_local_registration_keeps_existing_request_normalization(self):
        local = copy.deepcopy(self.migration)
        del local['inputs']['register_container']
        result = continuation.registration_request(local, self.attempt)
        self.assertEqual(result['model']['config'], str(self.attempt/'model/config.json'))
        self.assertEqual(result['precheck'], {'manifest': local['tasks']['precheck']['receipt']['path'],
                                           'sha256': local['tasks']['precheck']['receipt']['sha256']})


if __name__ == '__main__':
    unittest.main()
