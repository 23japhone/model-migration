"""数据集 prompt 列表内部补值；真实输入/请求/交接代码，外部效果不执行。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_customer_contract import customer, SCRIPTS
import test_prompt_resolution as entry_tests
from customer_workflow import contract, intent


def prompt(rows):
    return {'accuracy.datasets': rows}


def merged(raw, rows):
    return contract.normalize(intent.resolved(raw, prompt(rows)))


class DatasetPromptTests(unittest.TestCase):
    def test_each_default_marker_accepts_explicit_leaf_without_mutation(self):
        explicit = {'path': '/datasets/ceval', 'subset_list': ['b', 'a'], 'limit': 20}
        for key, expected in explicit.items():
            for marker in ('missing', None, -1):
                with self.subTest(key=key, marker=marker):
                    raw = customer()
                    row = raw['accuracy']['datasets'][0]
                    if marker == 'missing':
                        row.pop(key)
                    else:
                        row[key] = marker
                    patch_rows = [{'name': 'ceval', key: expected}]
                    before = copy.deepcopy((raw, patch_rows))
                    self.assertEqual(intent.conflicts(raw, prompt(patch_rows)), [])
                    self.assertEqual(merged(raw, patch_rows)['accuracy']['datasets'][0][key], expected)
                    self.assertEqual((raw, patch_rows), before)

    def test_prompt_unspecified_fields_never_clear_explicit_json(self):
        raw = customer()
        raw['accuracy']['datasets'][0].update(limit=100, subset_list=['b', 'a'])
        expected = contract.normalize(raw)
        for marker in ('missing', None, -1):
            rows = [{'name': 'ceval'}]
            if marker != 'missing':
                rows[0].update(path=marker, limit=marker, subset_list=marker)
            with self.subTest(marker=marker):
                self.assertEqual(merged(raw, rows), expected)
        for marker in (None, -1):
            self.assertEqual(merged(raw, marker), expected)

    def test_equal_explicit_fields_and_full_list_are_accepted(self):
        raw = customer()
        raw['accuracy']['datasets'][0].update(limit=20, subset_list=['a'])
        self.assertEqual(merged(raw, copy.deepcopy(raw['accuracy']['datasets'])), contract.normalize(raw))

    def test_conflicts_identify_each_dataset_leaf(self):
        raw = customer()
        raw['accuracy']['datasets'][0].update(limit=100, subset_list=['a', 'b'])
        rows = [{'name': 'ceval', 'path': '/other', 'limit': 20, 'subset_list': ['b', 'a']}]
        conflicts = intent.conflicts(raw, prompt(rows))
        self.assertEqual(len(conflicts), 3)
        for field in ('path', 'limit', 'subset_list'):
            self.assertTrue(any('ceval' in item and field in item for item in conflicts), conflicts)
        with self.assertRaisesRegex(ValueError, '冲突'):
            intent.resolved(raw, prompt(rows))

    def test_multiple_datasets_keep_names_order_and_untouched_fields(self):
        raw = customer()
        raw['accuracy']['datasets'].append({'name': 'gsm8k', 'limit': 50})
        result = merged(raw, [{'name': 'ceval', 'limit': 20}, {'name': 'gsm8k'}])
        self.assertEqual(result['accuracy']['datasets'], [
            {'name': 'ceval', 'path': '/data/ceval', 'limit': 20, 'subset_list': -1},
            {'name': 'gsm8k', 'path': '/data/gsm8k', 'limit': 50, 'subset_list': -1}])

    def test_name_addition_removal_rename_and_reorder_are_conflicts(self):
        raw = customer()
        raw['accuracy']['datasets'].append({'name': 'gsm8k'})
        for names in (['ceval'], ['gsm8k', 'ceval'], ['ceval', 'other'], ['ceval', 'gsm8k', 'other']):
            rows = [{'name': name, 'path': '/data/' + name} for name in names]
            with self.subTest(names=names):
                self.assertTrue(intent.conflicts(raw, prompt(rows)))

    def test_unselected_group_uses_prompt_selection_or_default_pair(self):
        for marker in ('missing', None, -1):
            raw = customer()
            if marker == 'missing':
                del raw['accuracy']['datasets']
            else:
                raw['accuracy']['datasets'] = marker
            with self.subTest(marker=marker):
                result = merged(raw, [{'name': 'gsm8k', 'limit': 20}])
                self.assertEqual([r['name'] for r in result['accuracy']['datasets']], ['gsm8k'])
                for no_choice in (None, -1):
                    result = merged(raw, no_choice)
                    self.assertEqual([r['name'] for r in result['accuracy']['datasets']], ['ceval', 'gsm8k'])

    def test_custom_dataset_patch_can_inherit_json_path(self):
        raw = customer()
        raw['accuracy']['datasets'] = [{'name': 'custom.v1', 'path': '/data/custom'}]
        result = merged(raw, [{'name': 'custom.v1', 'limit': 20}])
        self.assertEqual(result['accuracy']['datasets'][0],
            {'name': 'custom.v1', 'path': '/data/custom', 'limit': 20, 'subset_list': -1})
        raw['accuracy']['datasets'] = -1
        with self.assertRaises(ValueError):
            merged(raw, [{'name': 'custom.v1', 'limit': 20}])

    def test_partial_prompt_rejects_invalid_structure_and_explicit_values(self):
        bad_rows = [[], {}, [{}], [{'name': None}], [{'name': -1}], [{'name': True}],
                    [{'name': 'ceval'}, {'name': 'ceval'}], [{'name': 'ceval', 'unknown': 1}]]
        for field, values in {'path': ['', ' ', '-1', -1.0, True, [], 'relative'],
                              'limit': ['', ' ', '-1', -1.0, True, 0, [], {}],
                              'subset_list': ['', ' ', '-1', -1.0, True, [], ['a', 'a'], [1]]}.items():
            bad_rows.extend([{'name': 'ceval', field: value}] for value in values)
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                intent.parameters(prompt(rows))


class DatasetPromptEntryTests(unittest.TestCase):
    def setUp(self):
        self.entry = entry_tests.PromptResolutionTests()
        self.entry.setUp()
        self.addCleanup(self.entry.doCleanups)

    def test_entry_uses_same_merged_dataset_and_preserves_snapshot(self):
        raw = customer()
        before = copy.deepcopy(raw)
        code, result, workflow, preflight, *_ = self.entry.invoke(raw, prompt([{'name': 'ceval', 'limit': 20}]))
        self.assertEqual(code, 0, result)
        task = workflow.call_args.args[1]
        self.assertIs(task, preflight.call_args.args[1])
        self.assertEqual(task['accuracy']['datasets'][0]['limit'], 20)
        self.assertEqual(result['inputs'], contract.normalize(before))
        self.assertEqual(result['declared_inputs'], task)
        self.assertEqual(json.loads(self.entry.source.read_text()), before)

    def test_conflict_stops_before_installation_and_business(self):
        raw = customer()
        raw['accuracy']['datasets'][0]['limit'] = 100
        code, result, workflow, _, install, _ = self.entry.invoke(raw, prompt([{'name': 'ceval', 'limit': 20}]))
        self.assertEqual(code, 3, result)
        self.assertEqual(result['next_action']['owner'], 'user')
        self.assertIn('limit', result['next_action']['conflicts'][0])
        self.assertIn('ceval', result['next_action']['conflicts'][0])
        install.assert_not_called()
        workflow.assert_not_called()

    def test_invalid_json_cannot_be_repaired_by_prompt(self):
        for row in ({'name': 'custom'}, {'name': 'ceval', 'limit': 0}):
            raw = customer()
            raw['accuracy']['datasets'] = [row]
            code, result, workflow, _, install, _ = self.entry.invoke(raw,
                prompt([{'name': row['name'], 'path': '/data/custom', 'limit': 20}]))
            self.assertEqual(code, 2, result)
            self.assertFalse(self.entry.args.work_root.exists())
            install.assert_not_called()
            workflow.assert_not_called()

    def test_invalid_prompt_and_indexed_paths_do_not_install(self):
        for fields in (prompt([{'name': 'ceval', 'limit': True}]), {'accuracy.datasets.0.limit': 20}):
            code, result, workflow, _, install, _ = self.entry.invoke(customer(), fields)
            self.assertEqual(code, 2, result)
            self.assertFalse(self.entry.args.work_root.exists())
            install.assert_not_called()
            workflow.assert_not_called()

    def test_merged_path_guard_precedes_manifest(self):
        raw = customer()
        raw['accuracy']['datasets'][0]['path'] = -1
        code, result, workflow, *_ = self.entry.invoke(raw,
            prompt([{'name': 'ceval', 'path': str(self.entry.args.work_root)}]))
        self.assertEqual(code, 2, result)
        self.assertIn('重叠', result['reason'])
        self.assertFalse(self.entry.args.work_root.exists())
        workflow.assert_not_called()

    def test_equivalent_resume_passes_but_frozen_leaf_changes_fail(self):
        raw = customer()
        raw['accuracy']['datasets'][0]['path'] = -1
        selected = {'name': 'ceval', 'limit': 20}
        code, first, workflow, *_ = self.entry.invoke(raw, prompt([selected]))
        self.assertEqual(code, 0, first)
        self.entry.args.resume = workflow.call_args.args[0].path
        code, repeated, _, *_ = self.entry.invoke(raw, prompt([{**selected, 'path': None, 'subset_list': -1}]))
        self.assertEqual(code, 0, repeated)
        for key, changed in (('path', '/other'), ('limit', 30), ('subset_list', ['a'])):
            code, result, workflow, *_ = self.entry.invoke(raw, prompt([{**selected, key: changed}]))
            self.assertEqual(code, 2, result)
            self.assertIn('冻结', result['reason'])
            workflow.assert_not_called()

    def test_plan_ignores_prompt_without_effects(self):
        self.entry.args.plan = True
        from customer_workflow import context
        with patch.object(context, 'load', side_effect=AssertionError('plan does not read context')):
            code, result, workflow, _, install, _ = self.entry.invoke(customer(), prompt([{'name': 'ceval', 'limit': 20}]))
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'not_run')
        self.assertIsNone(result['inputs']['accuracy']['datasets'][0]['limit'])
        self.assertFalse(self.entry.args.work_root.exists())
        workflow.assert_not_called()
        install.assert_not_called()


class DatasetPromptConsumerTests(unittest.TestCase):
    def test_prompt_selection_reaches_preparation_precision_and_handoff(self):
        import sys
        sys.path.insert(0, str(SCRIPTS.parent / 'skills/vllm-migration-prepare/skills/vllm-dataset-prepare/scripts'))
        from dataset_prepare.request import normalize_request
        from dataset_prepare.assets import inspect_dataset
        from customer_workflow.compile import assets, accuracy
        from coordination.runtime.sources import ref
        from tuning_handoff.generation import evaluation
        from tuning_handoff.accuracy import validate
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = root / 'data'
            data.mkdir()
            for name in ('a', 'b'):
                (data / (name + '.jsonl')).write_text(''.join(json.dumps({'x': n}) + '\n' for n in range(30)))
            raw = customer()
            raw['accuracy']['datasets'][0]['path'] = -1
            task = merged(raw, [{'name': 'ceval', 'path': str(data), 'limit': 20, 'subset_list': ['b']}])
            selected = task['accuracy']['datasets'][0]
            rows = [{'name': name, 'split': 'test', 'source_file': name + '.jsonl', 'max_prompt_tokens': 8}
                    for name in ('a', 'b')]
            snapshot = root / 'catalog.json'
            snapshot.write_text(json.dumps({'inputs': {'dataset': selected, 'model': task['model']['model_name'],
                'model_path': '/model'}, 'evaluator_version': '1.2.3', 'subset_names': ['a', 'b'], 'subsets': rows,
                'data_files': {p.name: ref(p)['sha256'] for p in data.iterdir()}}))
            catalog = {'source': ref(snapshot), 'subsets': rows, 'local_path': str(data), 'preparation': {
                'source': 'local', 'path': str(data), 'format': 'jsonl', 'files': ['a.jsonl', 'b.jsonl']}}
            observations = {'datasets': {'ceval': catalog}, 'evaluator': {'version': '1.2.3'},
                            'model_resolution': {'request': {'path': '/model'}}}
            jobs = assets.dataset_jobs(task, observations)
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job['request']['selection'], {'offset': 0, 'file': 'b.jsonl', 'limit': 20})
            asset = inspect_dataset(normalize_request(job['request']))
            self.assertEqual(asset['selection']['count'], 20)
            job['prepared'] = {'asset': asset}
            config = accuracy.task_config(task, job, 'http://127.0.0.1:8000', False)
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config))
            receipt = root / 'prepared.json'
            receipt.write_text(json.dumps({'task_id': 'vllm-dataset-prepare', 'status': 'completed',
                                           'conclusions': {'assets': 'pass'}, 'asset': asset}))
            declared = {'dataset': job['id'], 'benchmark': 'ceval', 'subset': 'b', 'sample_ids': list(range(20))}
            baseline = {'inputs': {'dataset': ref(receipt), 'tasks': [declared],
                                  'evaluator': {'timeout_seconds': 100}}, 'detailed_report': {
                'conditions': {'evaluator_backend': 'evalscope', 'dataset': asset['identity'],
                               'selection': asset['selection']['sha256'], 'model': {}},
                'tasks': [{**declared, 'config': str(config_path)}],
                'files': {str(config_path): ref(config_path)['sha256']}}}
            handoff = {'model': {'name': task['model']['model_name']}, 'endpoint': 'http://127.0.0.1:8000'}
            plan = {'evaluation': evaluation([baseline], handoff)}
            validate(plan, [baseline], handoff)
            delivered = plan['evaluation']['datasets'][0]
            self.assertEqual((delivered['limit'], delivered['subset_list'], delivered['dataset']), (20, ['b'], str(data)))
            changed = copy.deepcopy(task)
            changed['accuracy']['datasets'][0]['limit'] = 10
            with self.assertRaisesRegex(ValueError, '样本选择'):
                assets.dataset_jobs(changed, observations)


if __name__ == '__main__':
    unittest.main()
