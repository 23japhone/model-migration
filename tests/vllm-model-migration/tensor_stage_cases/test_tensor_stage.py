"""从公开质量和 Token 回执选择第三遍范围，检查续接与 GPU 预检约束。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

FIXTURE = Path(__file__).resolve().parents[1] / 'token_alignment/test_stage_token.py'
spec = importlib.util.spec_from_file_location('tensor_stage_fixture', FIXTURE)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class TensorStage(unittest.TestCase):
    def setUp(self):
        self.source = fixture.TokenStage()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.root = self.source.directory
        self.put = self.source.put
        self.protocol = {'dataset': 'exam', 'model_identity': 'c' * 64}
        measurement = self.put('measure.json', {'format': 'task-quality-native-v1', 'hardware': 'npu',
                                               'protocol': self.protocol, 'scores': []})
        original = self.put('npu_origin.json', {'task_id': 'verify-agent-plus', 'status': 'completed',
                              'conclusions': {'evaluation_validity': 'pass'}, 'detailed_report': {'hardware': 'npu'},
                              'evidence': {'quality_evidence': measurement}})
        self.quality = self.source.quality(self.source.rows, {'measurement': measurement, 'receipt': original})
        self.source.report = self.quality
        self.case = {'subset': 'math', 'sample_id': 2}
        self.gpu = self.source.bundle('gpu', synthetic=True)
        self.npu = self.source.bundle('npu', synthetic=True)
        package = self.get(self.npu)
        record = package['cases'][0]['record']
        data = self.get(record)
        data['raw_logits'][0][0] += 2
        package['cases'][0]['record'] = self.put(Path(record['path']).name, data)
        self.npu = self.put('npu-bundle.json', package)
        summary, _, _ = self.source.run_stage([self.gpu, self.npu])
        self.token = summary['token_report']
        self.action = {'schema_version': '1.0','quality_report': self.quality, 'token_report': self.token,
                       'tensor_alignment': {}, 'evidence': []}

    def get(self, ref):
        return json.loads(Path(ref['path']).read_text())

    def invoke(self, *flags):
        spec = self.put('tensor.request.json', self.action)
        args = [sys.executable, '-B', str(fixture.STAGE), '--action', 'tensor', '--request', spec['path'],
                '--work-root', str(self.root / 'work'), '--run-id', 'tensor-case', *flags]
        proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return json.loads(proc.stdout)

    def inventory(self, side):
        return self.put(side + '-inventory.json', {'format': 'tensor-inventory-v1', 'hardware': side,
            'versions':{'torch':'2.10.0','vllm':'0.20.2','msprobe':'1.6.0'},
            'model_identity': self.protocol['model_identity'], 'calls': [{'id': 'block', 'owner': 'model.layers.0',
            'name': 'RMSNorm', 'kind': 'module', 'occurrence': 0, 'slots': [
                {'id': 'x', 'role': 'input', 'path': ['input_args',0]},
                {'id': 'y', 'role': 'output', 'path': ['output',0]}]}]})

    def test_legacy_frozen_selection_survives_missing_archived_per_sample_scores(self):
        quality = self.get(self.quality)
        quality['scores'][0].pop('sample_comparison')
        quality_ref = self.put('historical-quality.json', quality)
        token = self.get(self.token)
        scope = self.get(token['selection'])
        scope['quality_report'] = quality_ref
        token.update(quality_report=quality_ref, selection=self.put('historical-selection.json', scope))
        self.action.update(quality_report=quality_ref, token_report=self.put('historical-token.json', token))
        report = self.get(self.invoke()['tensor_report'])
        self.assertEqual((report['cases'][0]['sample_id'],report['cases'][0]['position']), (2,1))
        self.assertEqual(report['cases'][0]['result'], 'needs_scope')

    def test_known_case_failure_wins_over_uncovered_cases(self):
        scripts = str(fixture.STAGE.parent)
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        from tensor_stage.runner import aggregate
        for incomplete in ('needs_scope', 'awaiting_gpu', 'awaiting_npu','insufficient_evidence'):
            missing = {'result':incomplete}
            self.assertEqual(aggregate([missing, {'result':'mismatch'}]), 'mismatch')
            self.assertEqual(aggregate([{'result':'aligned'}, missing]), 'insufficient_evidence')

    def test_target_without_scope_is_identified_and_no_npu_launched(self):
        summary = self.invoke()
        row = self.get(summary['tensor_report'])['cases'][0]
        self.assertEqual((row['sample_id'], row['position']), (2, 1))
        self.assertEqual(row['result'], 'needs_scope')
        self.assertFalse(summary['npu_bundles'])

    def test_exact_inventories_generate_stable_plans_for_resume(self):
        self.action['evidence'] = [self.inventory(s) for s in ('gpu', 'npu')]
        summary = self.invoke()
        first = summary['plans'][0]
        plan = self.get(first)
        self.assertEqual(plan['case'], self.case)
        self.assertEqual(plan['prefix_token_ids'], plan['prompt_token_ids'])
        self.assertEqual(plan['triggers'], ['raw_logits'])
        self.assertEqual(plan['calls'][0]['gpu'], {'owner': 'model.layers.0', 'name': 'RMSNorm'})
        self.action['evidence'] = [first]
        second = self.invoke()
        self.assertEqual(first, second['plans'][0])
        self.assertEqual(self.get(second['tensor_report'])['cases'][0]['result'], 'awaiting_gpu')

    def test_plan_is_read_only(self):
        self.action['token_report'] = {'path': '/missing.json', 'sha256': 'a'*64}
        self.assertEqual(self.invoke('--plan')['status'], 'not_run')

    def test_missing_tensor_blocks_native_and_keeps_plan(self):
        self.action['evidence'] = [self.inventory(s) for s in ('gpu', 'npu')]
        plan = self.invoke()['plans'][0]
        obj = self.get(plan)
        bundle = {k: obj[k] for k in ('case','position','prefix_token_ids','model_identity','phase','rank')}
        bundle.update(format='tensor-bundle-v1', plan_sha256=plan['sha256'], hardware='gpu',
                      evidence_kind='synthetic', forward_id='f' * 32, tensors=[],
                      prediction={'token_id': 0, 'raw_logits':[0,1], 'processed_probs':[0.5,0.5]})
        self.action.update(evidence=[plan, self.put('invalid-gpu.json', bundle)],
                           native_request={'protocol': self.protocol})
        summary = self.invoke()
        row = self.get(summary['tensor_report'])['cases'][0]
        self.assertEqual(row['result'], 'invalid_gpu')
        self.assertFalse(list(Path(summary['manifest']).parent.rglob('tensor-run.request.json')))

    def runtime_bundle(self, plan_ref, side, raw_shift=0):
        import hashlib
        import torch
        plan = self.get(plan_ref)
        record = self.get(plan['predictions'][side])
        at = plan['position'] - 1
        raw = list(record['raw_logits'][at])
        raw[0] += raw_shift
        rows = []
        for slot in ('x','y'):
            file = self.root / (side + '-' + slot + '.pt')
            torch.save(torch.tensor([1.0, 2.0]), file)
            rows.append({'call':'block','slot':slot, 'path':file.name, 'shape':[2],
                         'layout':'strided','dtype':'float32','sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        bundle = {k:plan[k] for k in ('case','position','prefix_token_ids','model_identity','phase','rank')}
        bundle.update(format='tensor-bundle-v1', plan_sha256=plan_ref['sha256'], hardware=side,
                      evidence_kind='synthetic', forward_id=side+'-forward', tensors=rows,
                      prediction={'raw_logits':raw, 'processed_probs':record['processed_probs'][at],
                                  'token_id':record['output_token_ids'][at]})
        return self.put('tensor-'+side+'.json',bundle)

    def test_automatic_plan_preserves_one_sided_fp8_scale(self):
        import hashlib
        import torch
        gpu = self.get(self.inventory('gpu'))
        gpu['calls'][0]['slots'][0]['fp8_scaled'] = True
        self.action['evidence'] = [self.put('gpu-scaled.json',gpu),self.inventory('npu')]
        plan = self.invoke()['plans'][0]
        sources = [plan]
        for side, values in (('gpu',[2,4]),('npu',[1,2])):
            data = self.get(self.runtime_bundle(plan, side))
            row = data['tensors'][0]
            file = self.root / row['path']
            torch.save(torch.tensor(values).to(torch.float8_e4m3fn), file)
            row.update(dtype='float8_e4m3fn',sha256=hashlib.sha256(file.read_bytes()).hexdigest())
            if side == 'gpu':
                row['fp8_scale'] = .5
            sources.append(self.put(side+'-tensor.json',data))
        self.action['evidence'] = sources
        report = self.get(self.invoke()['tensor_report'])
        self.assertEqual((report['alignment_status'],report['target_status']), ('aligned','pass'))

    def test_inventory_preserves_shared_slots_when_only_one_device_lists_an_extra(self):
        gpu = self.get(self.inventory('gpu'))
        gpu['calls'][0]['slots'].append({'role':'weight','id':'extra','path':['parameters','weight']})
        self.action['evidence'] = [self.put('gpu-extra.json',gpu),self.inventory('npu')]
        report = self.get(self.invoke()['tensor_report'])
        row = report['cases'][0]
        plan = self.get(row['plan'])
        self.assertEqual([slot['id'] for slot in plan['calls'][0]['slots']], ['x','y'])
        self.assertIn('extra',str(plan['scope_uncovered']))
        import hashlib
        import torch
        plan_ref = row['plan']
        gpu = self.runtime_bundle(plan_ref,'gpu')
        npu = self.get(self.runtime_bundle(plan_ref,'npu'))
        tensor = npu['tensors'][1]
        file = self.root / tensor['path']
        torch.save(torch.tensor([1.,3.]), file)
        tensor['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
        self.action['evidence'] = [plan_ref,gpu,self.put('partial-known-fail.json',npu)]
        summary = self.invoke()
        self.assertEqual((summary['alignment_status'],summary['target_status']), ('mismatch','fail'))
        self.assertIn('extra', str(self.get(summary['tensor_report'])['cases'][0]['scope_uncovered']))


    def third_pass(self, raw_shift=0):
        self.action['evidence'] = [self.inventory(s) for s in ('gpu', 'npu')]
        plan = self.invoke()['plans'][0]
        self.action.update(evidence=[plan, self.runtime_bundle(plan,'gpu'), self.runtime_bundle(plan,'npu',raw_shift)],
                           tensor_alignment={'atol':0,'rtol':0})
        summary = self.invoke()
        return self.get(summary['tensor_report'])['cases'][0], plan

    def test_complete_pair_compares_with_bound_identity_and_reuses_when_tolerance_changes(self):
        row, plan = self.third_pass(0.005)
        self.assertEqual(row['result'], 'aligned', row)
        self.assertEqual(row['reproduction']['status'], 'reproduced')
        for side in ('gpu', 'npu'):
            replay = self.get(row[side + '_prediction_replay'])
            self.assertEqual(replay['result'], 'aligned')
            self.assertNotEqual(replay['evidence']['gpu'], replay['evidence']['npu'])
        self.assertFalse(row.get('npu_measurement'))
        self.action['tensor_alignment'] = {}
        summary = self.invoke()
        self.assertEqual(summary['plans'], [plan])
        self.assertEqual(self.get(summary['tensor_report'])['cases'][0]['result'], 'aligned')

    def test_target_that_no_longer_reproduces_stops_before_tensor_verdict(self):
        row, _ = self.third_pass(-2)
        self.assertEqual(row['result'], 'not_reproduced', row)
        self.assertNotIn('comparison', row)

    def test_same_cross_device_trigger_with_different_npu_prediction_is_not_reproduced(self):
        row, _ = self.third_pass(1)
        self.assertEqual(row['result'], 'not_reproduced', row)
        replay = row['reproduction']
        self.assertEqual((replay['status'], replay['hardware']), ('prediction_mismatch', 'npu'))
        self.assertEqual(set(self.get(replay['comparison'])['differences']), {'raw_logits'})
        self.assertNotIn('comparison', row)

    def test_changed_gpu_token_stops_before_native_capture(self):
        self.action['evidence'] = [self.inventory(s) for s in ('gpu', 'npu')]
        plan = self.invoke()['plans'][0]
        gpu = self.get(self.runtime_bundle(plan, 'gpu'))
        gpu['prediction']['token_id'] = 1
        self.action.update(evidence=[plan, self.put('changed-token.json', gpu)],
                           native_request={'protocol': self.protocol})
        summary = self.invoke()
        row = self.get(summary['tensor_report'])['cases'][0]
        self.assertEqual(row['result'], 'not_reproduced')
        replay = row['reproduction']
        self.assertEqual(replay['hardware'], 'gpu')
        self.assertEqual(set(self.get(replay['comparison'])['differences']), {'selected_token'})
        self.assertFalse(list(Path(summary['manifest']).parent.rglob('tensor-run.request.json')))

    def test_reproducible_synthetic_fixture_replays_second_token_and_cpu_coordinates(self):
        path = FIXTURE.parents[1] / 'tensor_stage_cases/build_tensor_stage_fixture.py'
        build = subprocess.run([sys.executable, '-B', str(path), '--output', str(self.root / 'generated')],
                               capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stderr)
        generated = self.get(json.loads(build.stdout))
        plan = self.get(generated['plan'])
        self.assertEqual((plan['phase'], plan['position'], plan['prefix_token_ids']), ('decode',2,[1,0,1]))
        states = {k:v['comparison_result'] for k,v in generated['runs'].items()}
        self.assertEqual(list(states.values()), ['insufficient_evidence','aligned','mismatch','aligned'])
        output = generated['runs']['04-gpu-outlier']
        self.assertEqual((output['alignment_status'], output['target_status']), ('mismatch', 'fail'))
        case = self.get(output['tensor_report'])['cases'][0]
        self.assertNotIn('simulated', case)
        self.assertEqual(case['evidence_kind'], {'gpu':'synthetic','npu':'synthetic'})
        physical = self.get(case['comparison'])
        self.assertEqual(physical['largest_error']['call'], 'add0')
        self.assertEqual(physical['largest_error']['slot'], 'y')
        self.assertEqual(physical['largest_error']['max_abs_error'], 4)
        self.assertEqual(physical['largest_error']['max_abs_coordinate'], [1,0])
        self.assertEqual(physical['cpu_check']['result'], 'mismatch')
        self.assertFalse(list((self.root / 'generated').rglob('tensor-run.request.json')))

    def test_early_stop_selects_no_single_sided_prediction(self):
        package = self.get(self.npu)
        shared = self.get(self.get(self.gpu)['cases'][0]['record'])
        shared['output_token_ids'] += shared['output_token_ids'][:1]
        for key in ('raw_logits','processed_probs'):
            shared[key] += shared[key][:1]
        package['cases'][0]['record'] = self.put('extra-step.json', shared)
        self.npu = self.put('new-npu.json', package)
        summary, _, token = self.source.run_stage([self.gpu, self.npu])
        self.assertEqual(token['cases'][0]['result'], 'early_stop')
        self.action['token_report'] = summary['token_report']
        result = self.invoke()
        row = self.get(result['tensor_report'])['cases'][0]
        self.assertEqual(row['source_result'], 'early_stop')
        self.assertEqual(result['alignment_status'], 'not_triggered')
        self.assertFalse(result['plans'])

    def test_unreadable_evidence_does_not_block_ready_case(self):
        prior = self.third_pass()[1]
        self.action['evidence'].append({'path':str(self.root/'unavailable'),'sha256':'a'*64})
        result = self.invoke()
        report = self.get(result['tensor_report'])
        self.assertTrue(report['uncovered'])
        self.assertEqual(result['plans'], [prior])
        self.assertEqual(report['cases'][0]['result'], 'aligned')

    def test_foreign_case_bundle_is_excluded_before_duplicate_detection(self):
        row, _ = self.third_pass()
        gpu = self.get(row['gpu'])
        foreign = self.put('other-case.json', {**gpu, 'case': {'subset': 'math', 'sample_id': 999}})
        self.action['evidence'].append(foreign)
        report = self.get(self.invoke()['tensor_report'])
        self.assertEqual(report['cases'][0]['result'], 'aligned')
        self.assertEqual(report['uncovered'][0]['source'], foreign)
        self.assertIn('case', report['uncovered'][0]['reason'])

        self.action['evidence'].append(self.put('second-gpu.json', gpu))
        report = self.get(self.invoke()['tensor_report'])
        self.assertEqual(report['cases'][0]['result'], 'insufficient_evidence')
        self.assertIn('多条匹配记录', report['cases'][0]['reason'])
        self.assertNotIn('comparison', report['cases'][0])

    def test_preserves_verified_files_when_peer_already_supplied(self):
        row, plan = self.third_pass()
        npu = self.get(row['npu'])
        npu['tensors'] = npu['tensors'][:1]
        self.action['evidence'] = [plan, row['gpu'], self.put('incomplete-import.json', npu)]
        result = self.invoke()
        item = self.get(result['tensor_report'])['cases'][0]
        self.assertEqual(item['result'], 'insufficient_evidence', item)
        partial = self.get(item['comparison'])
        self.assertEqual(partial['coverage']['npu']['verified_slots'], 1)
        self.assertFalse(result['npu_bundles'])

    def test_no_cpu_inference_when_add_scalar_arguments_are_unknown(self):
        path = FIXTURE.parents[1] / 'tensor_stage_cases/build_tensor_stage_fixture.py'
        spec = importlib.util.spec_from_file_location('scope_fixture', path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        scope = self.get(builder.common_scope(self.root))
        scope['model_identity'] = self.protocol['model_identity']
        call = scope['calls'][0]
        del call['gpu']['scalar_args']
        self.action['evidence'] = [self.put('unverified.json', scope)]
        plan = self.get(self.invoke()['plans'][0])
        self.assertNotIn('cpu_reference', plan['calls'][0])
        call['gpu']['scalar_args'] = {'alpha':2}
        call['npu']['scalar_args'] = {'alpha':2}
        self.action['evidence'] = [self.put('alpha.json', scope)]
        plan = self.get(self.invoke()['plans'][0])
        cpu = plan['calls'][0]['cpu_reference']
        self.assertEqual(cpu['attributes'], {'alpha':2})
        self.assertEqual(cpu['inputs'], {'a':'a','b':'b'})

    def test_incomplete_npu_and_failed_native_retry_preserve_partial_comparison(self):
        row, plan = self.third_pass()
        npu = self.get(row['npu'])
        npu['tensors'] = npu['tensors'][:1]
        self.action.update(evidence=[plan, row['gpu'], self.put('old-partial.json', npu)],
                           native_request={'protocol':self.protocol})
        result = self.invoke()
        item = self.get(result['tensor_report'])['cases'][0]
        self.assertEqual(item['result'], 'insufficient_evidence', item)
        self.assertIn('npu_measurement_issue', item)
        self.assertEqual(self.get(item['comparison'])['coverage']['npu']['verified_slots'], 1)
        self.assertEqual(len(list(Path(result['manifest']).parent.rglob('tensor-run.request.json'))), 1)

    def test_changing_software_versions_changes_plan_identity(self):
        inventories = [self.inventory(s) for s in ('gpu','npu')]
        versions = {'torch':'2.10.0','vllm':'0.20.2','msprobe':'1.6.0'}
        def submit(version):
            sources = []
            for item in inventories:
                obj = self.get(item)
                obj['versions'] = {**versions, 'torch':version if obj['hardware'] == 'npu' else versions['torch']}
                sources.append(self.put('versioned-' + obj['hardware'] + '.json', obj))
            self.action['evidence'] = sources
            return self.invoke()['plans'][0]
        first = submit('2.10.0')
        self.assertEqual(self.get(first)['runtime_versions'], {'gpu':versions,'npu':versions})
        self.assertNotEqual(submit('2.10.1')['sha256'], first['sha256'])
