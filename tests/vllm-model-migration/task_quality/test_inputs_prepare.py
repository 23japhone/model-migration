"""客户基线独立编译到正式数据准备和原生质量测量请求。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
from coordination.runtime import sources
from customer_workflow.quality import prepare


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf8')
    return sources.ref(path)


class PrepareIntegration(unittest.TestCase):
    def setUp(self):
        installation = patch('customer_workflow.quality.environment.call',
                             side_effect=AssertionError('离线准备测试意外触发环境安装'))
        installation.start()
        self.addCleanup(installation.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = self.tmp = Path(tmp.name).resolve()
        self.data = root / 'quiz'
        self.data.mkdir()
        self.files = {}
        self.scopes = []
        for name in ('a', 'b'):
            file = self.data / (name + '.jsonl')
            file.write_text('{"id":"first","q":"test","a":"yes"}\n{"id":"second","q":"test2","a":"no"}\n')
            self.files[file.name] = sources.ref(file)['sha256']
            self.scopes.append({'name': name, 'source_file': file.name, 'split': 'test'})
        repo = 'https://huggingface.co/datasets/org/quiz'
        revision = 'a' * 40
        url = 'https://huggingface.co/api/datasets/org/quiz/refs'
        snapshot = save(root / 'official.json', {'revision': revision, 'repository': repo, 'files': self.files})
        api = save(root / 'refs.json', {'url': url, 'body': {'tags': [],
                                  'branches': [{'name': 'main', 'targetCommit': revision}]}})
        meta = url.removesuffix('/refs')
        default = save(root / 'default.json', {'url': meta, 'body': {'sha': revision}})
        native = save(root / 'native.json', {'format': 'evalscope-quality-catalog-v1', 'name': 'quiz',
                      'evaluator_version': '1.2', 'subset_names': ['a', 'b'], 'eval_split': 'test',
                      'metadata': {'evaluation_version': 'v1.1', 'few_shot_num': 4, 'metric_list': ['acc'], 'prompt_template': 'Native {q}'},
                      'adapter_source': sources.ref(Path(__file__).resolve()), 'generation_defaults': {'max_tokens': 27}})
        contents = {}
        for index, name in enumerate(self.files):
            uri = repo + '/resolve/' + revision + '/' + name
            contents[uri] = save(root / ('upstream' + str(index)), {'url': uri, 'payload': sources.ref(self.data / name)})
        spec = {'content_sources': contents, 'repository': repo, 'snapshot_binding': snapshot,
                'responses': {url: api, meta: default},
                'preparation': {'path': str(self.data), 'source': 'local', 'recipe': 'generic',
                                'required_fields': ['q', 'a'], 'format': 'jsonl'},
                'subsets': self.scopes, 'catalog': native}
        handoff = save(root / 'handoff.json', {'model': {'name': 'model', 'files': {'tokenizer': 'abc'}},
                                                'endpoint': 'http://127.0.0.1:8080'})
        deployment = save(root / 'verify.json', {'evidence': {'handoff': handoff['path']},
                                             'detailed_report': {'handoff_sha256': handoff['sha256']}})
        self.ctx = {'deployment_receipt': deployment, 'quality_sources': {'quiz': spec},
                    'evaluator': {'version': '1.2', 'python': sys.executable}, 'evidence': []}
        self.task = {'model': {'model_name': 'model'}, 'accuracy': {'datasets': [{'name': 'quiz', 'limit': 1}]}}
        self.base = {'dataset': 'quiz', 'evaluator': 'evalscope', 'evaluator_version': '1.2',
                     'prompt_config': {'request_format': 'chat', 'enable_thinking': True},
                     'generation_config': {'temperature': 0.3, 'stop': 'END'},
                     'scores': [{'metric': 'accuracy', 'gpu_score': 0.9}]}

    def test_foreign_gpu_protocol_does_not_filter_or_configure_npu_measurement(self):
        self.ctx['quality_sources']['quiz']['evaluator_environment'] = self.ctx['evaluator']
        self.base.update(evaluator_version='0.42', task_name='gpu-only', task_revision='gpu-rev',
                         scoring_config={'scorer_name': 'other'}, prompt_config={'request_format': 'completion'},
                         generation_config={'temperature': 0.8, 'max_tokens': 777})
        req = prepare(self.base, self.task, self.ctx, self.tmp/'native')['native_request']
        self.assertEqual('1.2', req['protocol']['evaluator_version'])
        self.assertEqual('quiz', req['protocol']['task_name'])
        self.assertEqual('chat', req['protocol']['prompt_config']['request_format'])
        self.assertEqual(27, req['protocol']['generation_config']['max_tokens'])
        self.assertNotIn('declaration', req['protocol']['scoring_config'])

    def test_all_real_samples_native_defaults_and_reuse(self):
        root = self.tmp / 'quality'
        self.ctx['quality_sources']['quiz'].update(
            prompt_config={'request_format': 'chat', 'enable_thinking': True},
            generation_config={'temperature': 0.3, 'stop': 'END'})
        result = prepare(self.base, self.task, self.ctx, root)
        self.assertEqual(result['protocol']['subsets'], ['a', 'b'])
        self.assertEqual(result['protocol']['task_revision'], 'v1.1')
        req = result['native_request']
        cfg = sources.bound(req['measurement']['evaluator']['config'])
        self.assertIs(cfg['collect_perf'], False)
        self.assertEqual(cfg['generation_config']['stop_seqs'], ['END'])
        self.assertEqual(cfg['generation_config']['max_tokens'], 27)
        self.assertEqual(cfg['generation_config']['extra_body']['chat_template_kwargs'], {'enable_thinking': True})
        self.assertEqual(cfg['dataset_args']['quiz']['few_shot_num'], 4)
        self.assertEqual(req['measurement']['tasks'][1]['sample_ids'], [0, 1])
        self.assertEqual(cfg['limit'], None)
        self.base['scores'][0].update(gpu_score=0.85, target={'max_drop': 0},
                                      sample_results=[{'sample_id': 'first', 'score': 0}])
        self.assertEqual(prepare(self.base, self.task, self.ctx, root), result)

    def test_cannot_claim_ordinary_limited_data_are_a_full_official_snapshot(self):
        self.ctx['quality_sources']['quiz']['snapshot_binding'] = save(self.tmp / 'short.json', {
            'revision': 'a' * 40, 'repository': 'https://huggingface.co/datasets/org/quiz',
            'files': {'a.jsonl': self.files['a.jsonl']}})
        with self.assertRaisesRegex(ValueError, '完整文件清单'):
            prepare(self.base, self.task, self.ctx, self.tmp / 'quality')

    def test_absent_requested_subset_keeps_available_item_and_omits_joint_total(self):
        self.base['subset_list'] = ['a', 'absent']
        self.base['scores'].extend({'metric':'accuracy', 'subset':subset, 'gpu_score':1}
                                   for subset in self.base['subset_list'])
        native = prepare(self.base, self.task, self.ctx, self.tmp/'partial')['native_request']
        self.assertEqual(['a'], native['protocol']['subsets'])
        self.assertEqual(['a'], [row['subset'] for row in native['scores']])

    def test_changed_local_files_cannot_pass_cached_preparation(self):
        root = self.tmp / 'quality'
        prepare(self.base, self.task, self.ctx, root)
        (self.data / 'b.jsonl').write_text('{"id":"changed","q":"test","a":"yes"}\n')
        with self.assertRaises(ValueError):
            prepare(self.base, self.task, self.ctx, root)

    def test_minimal_declaration_preserves_native_unset_output_budget(self):
        ref = self.ctx['quality_sources']['quiz']['catalog']
        catalog = sources.bound(ref)
        catalog['generation_defaults'] = {'temperature': 0}
        self.ctx['quality_sources']['quiz']['catalog'] = save(self.tmp / 'uncapped.json', catalog)
        base = {'dataset': 'quiz', 'evaluator': 'evalscope', 'evaluator_version': '1.2',
                'scores': [{'metric': 'accuracy', 'gpu_score': 0}]}
        request = prepare(base, self.task, self.ctx, self.tmp / 'quality')['native_request']
        generation = sources.bound(request['measurement']['evaluator']['config'])['generation_config']
        self.assertNotIn('max_tokens', generation)

    def test_explicit_variant_needs_registered_task_implementation(self):
        self.ctx['quality_sources']['quiz']['task_name'] = 'unregistered-variant'
        self.base['task_name'] = 'unregistered-variant'
        with self.assertRaisesRegex(ValueError, '任务变体'):
            prepare(self.base, self.task, self.ctx, self.tmp / 'quality')

    def public_f1_run(self):
        spec = {'selector': {'name': 'f1', 'aggregation': 'mean', 'dimensions': {'average':'macro'}},
                'unit': 'ratio', 'direction': 'higher', 'sample_metric': 'f1', 'sample_kind': 'numeric'}
        source = self.ctx['quality_sources']['quiz']
        catalog = sources.bound(source['catalog'])
        catalog['metadata']['metric_list'] = ['f1']
        source['catalog'] = save(self.tmp/'catalog-f1.json', catalog)
        source['metrics'] = {'F1': spec}
        base = {**self.base, 'scores': [{'metric': 'F1', 'gpu_score': 0.5}]}
        compiled = prepare(base, self.task, self.ctx, self.tmp/'gpu-launch')['native_request']
        del source['metrics']
        actual = sources.bound(compiled['measurement']['evaluator']['config'])
        config = save(self.tmp/'gpu-config.json', actual)
        counts = {'requested':2, 'succeeded':2, 'errored':0}
        count_by_subset = {name: dict(counts) for name in ('a','b')}
        metric = {'identity':spec['selector'], 'semantics':{'kind':'quality','raw_unit':'ratio','direction':'higher_is_better'},
                  'score':0.5, 'num':4, 'categories':[{'subsets':[{'name':name,'score':0.5,'num':2} for name in ('a','b')]}]}
        report = save(self.tmp/'gpu-report.json', {'schema_version':2, 'model_name':actual['model_id'], 'dataset_name':'quiz',
            'metrics':[metric], 'execution_summary':{'requested':4,'succeeded':4,'errored':0,'incomplete':False,'subsets':count_by_subset}})
        original = save(self.tmp/'gpu-run.json', {'status':'completed','hardware':'gpu','run_id':'historical',
                        'protocol':compiled['protocol'], 'sample_counts':dict.fromkeys(count_by_subset, 2),
                        'files':{item['path']:item['sha256'] for item in (config,report)}})
        index = save(self.tmp/'gpu-index.json', {'format':'evalscope-gpu-history-v1','source':original,'report':report,'config':config})
        return base, index, compiled['protocol']

    def test_metric_scale_and_partial_mapping_reach_native_request(self):
        spec = {'selector': {'name': 'mae', 'aggregation': 'mean', 'dimensions': {}},
                'unit': 'value', 'direction': 'lower', 'sample_metric': 'mae', 'sample_kind': 'numeric',
                'scale_type': 'ratio'}
        self.ctx['quality_sources']['quiz']['metrics'] = {'MAE': spec}
        self.base['scores'] = [{'metric': 'MAE', 'gpu_score': 4}, {'metric': 'unavailable', 'gpu_score': 1}]
        native = prepare(self.base, self.task, self.ctx, self.tmp/'mae')['native_request']
        self.assertEqual(spec, native['protocol']['metrics']['MAE'])
        self.assertEqual([{**spec, 'metric': 'MAE', 'subset': None}], native['scores'])

    def test_public_f1_uses_bound_historical_dataset_after_latest_moves(self):
        from customer_workflow.quality.resolution import resolve
        base, archive, historical = self.public_f1_run()
        source = self.ctx['quality_sources']['quiz']
        repo = 'https://huggingface.co/api/datasets/org/quiz'
        new = 'b' * 40
        source['responses'] = {url: save(self.tmp / ('now' + str(i)), {'url': url, 'body': body})
            for i, (url, body) in enumerate([(repo + '/refs', {'tags': [], 'branches':[{'name':'main','targetCommit':new}]}),
                                              (repo, {'sha':new})])}
        latest_binding = sources.bound(source['snapshot_binding'])
        latest_binding.update(revision=new, files={n:'b' * 64 for n in self.files})
        source['snapshot_binding'] = save(self.tmp/'latest-binding.json', latest_binding)
        self.assertEqual(new, resolve(base, source, self.tmp/'new-ordinary')['dataset_revision'])
        root = self.tmp / 'historical-npu'
        plan = prepare(base, self.task, self.ctx, root, public_gpu=archive)
        self.assertEqual('quiz', plan['protocol']['dataset'])
        self.assertEqual('a'*40, plan['protocol']['dataset_revision'])
        self.assertEqual(historical['metrics']['F1'], plan['protocol']['metrics']['F1'])
        self.assertEqual('macro', plan['native_request']['scores'][0]['selector']['dimensions']['average'])
        self.assertIn(archive, sources.read(root/'release-selection.json')['evidence'])
        downloaded = root / 'official-data' / historical['dataset_revision'] / 'a.jsonl'
        self.assertEqual(self.files['a.jsonl'], sources.ref(downloaded)['sha256'])

    def test_public_archive_change_before_historical_replay_rejects_it(self):
        base, archive, _ = self.public_f1_run()
        config_path = Path(sources.bound(archive)['config']['path'])
        config_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'GPU|摘要'):
            prepare(base, self.task, self.ctx, self.tmp/'changed', public_gpu=archive)

    def test_unpinned_gpu_snapshot_is_never_used_as_a_dataset_url(self):
        base, archive, _ = self.public_f1_run()
        index = sources.bound(archive)
        original = sources.bound(index['source'])
        original['protocol']['dataset_revision'] = 'main'
        index['source'] = save(self.tmp/'mutable-run.json', original)
        archive = save(self.tmp/'mutable-index.json', index)
        with self.assertRaisesRegex(ValueError, '不可变'):
            prepare(base, self.task, self.ctx, self.tmp/'mutable', public_gpu=archive)
