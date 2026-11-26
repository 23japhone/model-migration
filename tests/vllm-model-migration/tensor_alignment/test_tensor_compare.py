"""逐调用张量比较的公开 CLI 与隔离错误行为。"""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[3]
CLI = next((REPO / 'skills').rglob('vllm-precision-compare/scripts/compare.py'))
try:
    import torch
except ImportError:
    torch = None


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')
    return ref(path)


@unittest.skipUnless(torch, '原生 pt 比较需要 PyTorch')
class TensorComparison(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.identity = {'case': {'subset': 'math', 'sample_id': '3'}, 'position': 2,
                         'model_identity': '0123456789abcdef' * 4, 'prompt_token_ids': [11],
                         'prefix_token_ids': [11, 22]}
        self.call = {'id': 'block.linear', 'kind': 'api', 'occurrence': 0,
                     'gpu': {'owner': 'layer', 'name': 'linear'},
                     'npu': {'owner': 'layer', 'name': 'linear'},
                     'cpu_reference': {'op': 'linear', 'inputs': {'x': 'x', 'weight': 'w'}, 'output': 'y'},
                     'slots': [{'id': k, 'role': role, 'gpu': path, 'npu': path}
                               for k, role, path in [('x', 'input', ['input_args', 0]),
                                                     ('w', 'weight', ['input_args', 1]),
                                                     ('y', 'output', ['output', 0])]]}
        self.plan = {'format': 'tensor-plan-v1', **self.identity, 'calls': [self.call]}
        self.policy = {'atol': 0, 'rtol': 0}
        self.sequence = 0

    def bundle(self, hardware, tensors):
        folder = self.root / (hardware + str(self.sequence))
        folder.mkdir()
        rows = []
        for entry, tensor in tensors.items():
            call, slot, ordinal = entry if isinstance(entry, tuple) else ('block.linear', entry, 1)
            path = folder / (str(len(rows)) + '-' + slot + '.pt')
            torch.save(tensor, path)
            rows.append({'call': call, 'slot': slot, **ref(path), 'path': path.name,
                         'ordinal': ordinal, 'layout': 'strided', 'shape': list(tensor.shape),
                         'dtype': str(tensor.dtype).removeprefix('torch.')})
        data = {'format': 'tensor-bundle-v1', 'hardware': hardware, **self.identity,
                'evidence_kind': 'synthetic', 'plan_sha256': self.plan_ref['sha256'],
                'forward_id': hardware + '-forward', 'phase': 'prefill' if self.identity['position'] == 1 else 'decode',
                'rank': 0, 'tensors': rows}
        return write(folder / 'bundle.json', data)

    def compare(self, gpu, npu=None, update=None):
        self.sequence += 1
        self.plan_ref = write(self.root / 'plan.json', self.plan)
        request = {'plan': self.plan_ref, 'gpu': self.bundle('gpu', gpu), 'tensor_alignment': self.policy}
        if npu is not None:
            request['npu'] = self.bundle('npu', npu)
        if update:
            update(request)
        input_path = self.root / 'request.json'
        write(input_path, request)
        output = self.root / ('report' + str(self.sequence) + '.json')
        proc = subprocess.run([sys.executable, '-B', str(CLI), '--action', 'tensor', '--request',
                               str(input_path), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return json.loads(output.read_text())

    @staticmethod
    def example():
        return {'x': torch.tensor([[1., 2.]]), 'w': torch.tensor([[3., 4.]]), 'y': torch.tensor([[11.]])}

    def test_real_files_are_compared_and_recomputed(self):
        data = self.example()
        report = self.compare(data, data)
        self.assertEqual(report['result'], 'aligned')
        self.assertNotIn('hardware_target_status', report)
        self.assertEqual(report['calls'][0]['cpu_check']['result'], 'aligned')
        report = self.compare(data, {**data, 'y': torch.tensor([[8.]])})
        self.assertEqual(report['result'], 'mismatch')
        self.assertEqual(report['first_exceedance']['slot'], 'y')
        self.assertEqual(report['largest_error']['max_abs_error'], 3)
        cpu = report['calls'][0]['cpu_check']
        self.assertEqual(cpu['gpu']['result'], 'pass')
        self.assertEqual(cpu['npu']['result'], 'fail')

    def test_one_sided_preflight_and_incomplete_coverage(self):
        data = self.example()
        report = self.compare(data)
        self.assertEqual((report['result'], report['capture_status']), ('insufficient_evidence', 'complete'))
        self.assertEqual(report['missing_devices'], ['npu'])
        report = self.compare(data, {'x': data['x']})
        self.assertEqual(report['result'], 'insufficient_evidence')
        self.assertEqual(report['coverage']['npu']['verified_slots'], 1)

    def test_distinct_input_disqualifies_common_cpu(self):
        data = self.example()
        report = self.compare(data, {**data, 'x': torch.tensor([[1., 1.]])})
        result = report['calls'][0]['cpu_check']
        self.assertEqual(result['basis'], 'per_device_inputs')
        self.assertEqual(result['different_inputs'], ['x'])
        self.assertEqual(result['gpu']['result'], 'pass')
        self.assertEqual(result['npu']['max_abs_error'], 4)

    def test_both_devices_can_share_a_wrong_result(self):
        data = {**self.example(), 'y': torch.tensor([[12.]])}
        report = self.compare(data, data)
        self.assertEqual(report['result'], 'aligned')
        self.assertEqual(report['target_status'], 'pass')
        self.assertEqual(report['cpu_check']['result'], 'mismatch')

    def test_cpu_alpha_matches_real_semantics_and_missing_scope(self):
        self.call['cpu_reference'] = {'op':'add', 'inputs':{'a':'x','b':'w'}, 'output':'y','attributes':{'alpha':2}}
        data = {**self.example(), 'y':torch.tensor([[7.,10.]])}
        self.assertEqual(self.compare(data, data)['cpu_check']['common_reference'], 'aligned')
        self.call['cpu_reference']['attributes']['alpha'] = 1
        self.assertEqual(self.compare(data, data)['cpu_check']['common_reference'], 'mismatch')
        self.call['cpu_reference']['op'] = 'unknown_fusion'
        report = self.compare(data, data)
        self.assertEqual(report['result'], 'aligned')
        self.assertEqual(report['target_status'], 'pass')

    def test_explicit_oracle_uses_observed_scalars_from_each_device(self):
        data = {**self.example(), 'y':torch.tensor([[4.,6.]])}
        self.call['cpu_reference'] = {'op':'add','inputs':{'a':'x','b':'w'},'output':'y'}
        for side in ('gpu','npu'):
            self.call[side].update(name='Torch.add',scalar_args={})
        self.call['npu']['scalar_args'] = {'alpha':2}
        report = self.compare(data, data)
        self.assertEqual(report['result'], 'aligned')
        cpu = report['calls'][0]['cpu_check']
        self.assertEqual(cpu['result'], 'not_evaluated', cpu)
        self.assertIn('alpha',cpu['reason'])
        self.assertIn('npu',cpu['reason'])
        self.assertEqual(report['target_status'],'pass')
        self.call['gpu']['scalar_args'] = {'alpha':2}
        self.call['cpu_reference']['attributes'] = {'alpha':2}
        actual = {**data, 'y':torch.tensor([[7.,10.]])}
        self.assertEqual(self.compare(actual, actual)['calls'][0]['cpu_check']['result'],'aligned')
        self.call['gpu']['scalar_args'] = {}
        self.assertEqual(self.compare(actual, actual)['calls'][0]['cpu_check']['result'],'not_evaluated')

    def test_explicit_softmax_oracle_rejects_different_axis_or_dtype(self):
        data = torch.tensor([[0.,0.],[0.,0.]])
        self.call['gpu'].update(name='Functional.softmax', scalar_args={'dim':0})
        self.call['npu'].update(name='Functional.softmax', scalar_args={'dim':1})
        for scalar_args in ({'dim':1},{'dim':0,'dtype':'float16'}):
            self.call['npu']['scalar_args'] = scalar_args
            cpu = self.cpu_operation('softmax', {'x':data}, {'dim':0}, torch.full_like(data, .5))
            self.assertEqual(cpu['result'],'not_evaluated',cpu)

    def test_standard_gelu_and_layer_norm_defaults_override_conflicting_recipe(self):
        x = torch.tensor([[1.,3.]], dtype=torch.float64)
        for api, op, inputs, attrs, conflicting in (
                ('Functional.gelu', 'gelu', {'x':x}, {'approximate':'none'}, {'approximate':'tanh'}),
                ('Functional.layer_norm', 'layer_norm', {'x':x}, {'normalized_shape':[2],'eps':1e-5},
                 {'normalized_shape':[2],'eps':.2})):
            with self.subTest(api=api):
                observed = {'normalized_shape':[2]} if op == 'layer_norm' else {}
                for side in ('gpu','npu'):
                    self.call[side].update(name=api, scalar_args=observed)
                result = (torch.nn.functional.gelu(x) if op == 'gelu' else
                          torch.nn.functional.layer_norm(x, (2,)))
                self.assertEqual(self.cpu_operation(op,inputs,attrs,result)['result'], 'aligned')
                cpu = self.cpu_operation(op,inputs,conflicting,result)
                self.assertEqual(cpu['result'], 'not_evaluated', cpu)
                self.assertIn('实际标量', cpu['reason'])

    def test_standard_sdpa_causality_and_rms_norm_dtype_dependent_default(self):
        x = torch.ones(1,2,dtype=torch.float64)
        for side in ('gpu','npu'):
            self.call[side].update(name='Functional.rms_norm', scalar_args={})
        norm = self.cpu_operation('rms_norm', {'x':x,'weight':x[0]}, {'eps':1e-5},x,('weight',))
        self.assertEqual(norm['result'], 'not_evaluated', norm)
        for side in ('gpu','npu'):
            self.call[side].update(name='Functional.scaled_dot_product_attention', scalar_args={})
        q = torch.zeros(1,1,1,1,dtype=torch.float64)
        k = torch.zeros(1,1,2,1,dtype=torch.float64)
        v = torch.tensor([[[[1.],[5.]]]],dtype=torch.float64)
        attrs = {'layout':'BHSD','kv_source':'full_prefix','scale':None,'dropout_p':0,
                 'mask_kind':'none','causal_alignment':'none'}
        inputs = {'q':q,'k':k,'v':v}
        expected = torch.nn.functional.scaled_dot_product_attention(q,k,v)
        self.assertEqual(self.cpu_operation('scaled_dot_product_attention',inputs,attrs,expected)['result'], 'aligned')
        cpu = self.cpu_operation('scaled_dot_product_attention',inputs,{**attrs,'causal_alignment':'lower_right'},expected)
        self.assertEqual(cpu['result'], 'not_evaluated', cpu)

    def cpu_operation(self, op, inputs, attrs, expected, weights=()):
        self.call['cpu_reference'] = {'op':op, 'inputs':{name:name for name in inputs},
                                      'output':'output', 'attributes':attrs}
        slots = []
        for i, key in enumerate([*inputs, 'output']):
            role = 'weight' if key in weights else ('output' if key == 'output' else 'input')
            path = ['output',0] if key == 'output' else ['input_args',i]
            slots.append({'id':key, 'role':role, 'gpu':path, 'npu':path})
        self.call['slots'] = slots
        data = {**inputs, 'output':expected}
        result = self.compare(data, data)
        return result['calls'][0]['cpu_check']

    def test_sdpa_decode_requires_full_history_and_causal_origin(self):
        q = torch.zeros(1,1,1,1)
        k = torch.zeros(1,1,2,1)
        v = torch.tensor([[[[1.],[5.]]]])
        attrs = {'layout':'BHSD', 'kv_source':'full_prefix', 'dropout_p':0, 'scale':None,
                 'mask_kind':'none', 'causal_alignment':'lower_right'}
        args = {'q':q, 'k':k, 'v':v}
        def use(a, p, y):
            return self.cpu_operation('scaled_dot_product_attention', a, p, torch.tensor([[[[y]]]]))
        self.assertEqual(use(args, attrs, 3.)['result'], 'aligned')
        self.assertEqual(use(args, {**attrs,'causal_alignment':'upper_left'}, 1.)['result'], 'aligned')
        missing = use({**args,'k':k[:,:,:1].clone(), 'v':v[:,:,:1].clone()}, attrs, 1.)
        self.assertEqual(missing['result'], 'not_evaluated')
        self.assertIn('全部有效前文', missing['reason'])
        self.assertEqual(use(args, {**attrs,'dropout_p':.5}, 3.)['result'], 'not_evaluated')
        self.assertEqual(use(args, {**attrs,'kv_source':'current_token'}, 3.)['result'], 'not_evaluated')

    def test_sdpa_square_mask_and_medium_batched_heads(self):
        generator = torch.Generator().manual_seed(23)
        length = 256
        self.identity.update(position=1, prompt_token_ids=list(range(length)), prefix_token_ids=list(range(length)))
        self.plan.update(self.identity)
        q, k, v = [torch.randn((1,4,length,16), dtype=torch.float64, generator=generator) for _ in range(3)]
        mask = torch.ones(length,length, dtype=torch.bool).tril()
        weights = ((q @ k.transpose(-2,-1)) * .25).masked_fill(~mask, -torch.inf).softmax(dim=-1)
        attrs = {'layout':'BHSD','kv_source':'full_prefix','dropout_p':0,'scale':.25,
                 'mask_kind':'bool_keep','causal_alignment':'none'}
        self.policy = {'atol':1.e-12,'rtol':1.e-12}
        out = self.cpu_operation('scaled_dot_product_attention', {'q':q,'k':k,'v':v,'mask':mask}, attrs, weights @ v)
        self.assertEqual(out['result'], 'aligned', out)
        additive = torch.zeros_like(mask, dtype=torch.float64).masked_fill(~mask, -torch.inf)
        out = self.cpu_operation('scaled_dot_product_attention', {'q':q,'k':k,'v':v,'mask':additive},
                                 {**attrs,'mask_kind':'additive'}, weights @ v)
        self.assertEqual(out['result'], 'aligned', out)

    def test_layer_norm_and_gelu_cpu_modes(self):
        x = torch.tensor([[2.,4.],[6.,10.]], dtype=torch.float64)
        w, b = torch.tensor([1.,2.]), torch.tensor([.5,-.5])
        self.policy = {'atol':1.e-12,'rtol':1.e-12}
        for inputs in ({'x':x,'weight':w,'bias':b}, {'x':x}):
            norm = torch.nn.functional.layer_norm(x, (2,), w.double() if 'weight' in inputs else None,
                                                  b.double() if 'bias' in inputs else None, 1.e-5)
            attrs = {'normalized_shape':[2],'eps':1.e-5}
            result = self.cpu_operation('layer_norm',inputs,attrs,norm,('weight','bias'))
            self.assertEqual(result['result'], 'aligned', result)
        for mode in ('none','tanh'):
            expected = torch.nn.functional.gelu(x, approximate=mode)
            result = self.cpu_operation('gelu', {'x':x}, {'approximate':mode}, expected)
            self.assertEqual(result['result'], 'aligned', result)

    def test_threshold_boundary_and_missing_threshold(self):
        data = self.example()
        changed = {**data, 'y': torch.tensor([[12.]])}
        self.policy = {'atol': 1, 'rtol': 0}
        self.assertEqual(self.compare(data, changed)['result'], 'aligned')
        self.policy = {'atol': .99, 'rtol': 0}
        self.assertEqual(self.compare(data, changed)['result'], 'mismatch')
        self.policy = {}
        self.assertEqual(self.compare(data, changed)['result'], 'mismatch')

    def test_actual_dtype_defaults_and_partial_explicit_tolerance(self):
        self.call.pop('cpu_reference')
        self.policy = {}
        defaults = [(torch.float64, 1e-7, 1e-7), (torch.float32, 1e-5, 1.3e-6),
                    (torch.float16, 1e-5, 1e-3), (torch.bfloat16, 1e-5, .016),
                    (torch.float8_e4m3fn, 2**-9, .125), (torch.float8_e5m2, 2**-16, .25),
                    (torch.int64, 0, 0), (torch.bool, 0, 0)]
        slots = [f's{i}' for i in range(len(defaults))]
        self.call['slots'] = [{'id': k, 'role': 'output', 'gpu': ['output',i], 'npu':['output',i]}
                              for i,k in enumerate(slots)]
        data = {k:torch.tensor([0,1]).to(dtype) for k,(dtype,_,_) in zip(slots, defaults)}
        report = self.compare(data, data, update=lambda req: req.pop('tensor_alignment'))
        self.assertEqual((report['result'], report['target_status']), ('aligned','pass'))
        self.assertEqual([s['tolerances'] for s in report['calls'][0]['slots']],
                         [{'atol':a,'rtol':r} for _,a,r in defaults])
        self.policy = {'rtol': 0}
        changed = {**data, 's4':torch.tensor([2**-9,1]).to(torch.float8_e4m3fn)}
        self.assertEqual(self.compare(data, changed)['target_status'], 'pass')
        self.policy = {'atol': 0}
        self.assertEqual(self.compare(data, changed)['target_status'], 'fail')

    def test_fp8_block_scales_dequantize_each_side_and_bound_near_zero(self):
        self.call.pop('cpu_reference')
        self.call['slots'] = [self.call['slots'][-1]]
        self.call['slots'][0]['fp8_scaled'] = True
        self.policy = {}
        dtype = torch.float8_e4m3fn
        gpu = {'y':torch.tensor([0,1,0,1]).to(dtype)}
        npu = {'y':torch.tensor([2**-8,2,2**-10,0.5]).to(dtype)}
        def scale(req, incomplete=False):
            for side,multipliers in [('gpu',[1,4]), ('npu',[.5,8])]:
                file = Path(req[side]['path'])
                package = json.loads(file.read_text())
                if not (incomplete and side == 'npu'):
                    package['tensors'][0]['fp8_scale'] = {'block_shape':[2],'values':multipliers}
                req[side] = write(file, package)
        report = self.compare(gpu, npu, update=scale)
        self.assertEqual(report['target_status'], 'pass', report['calls'])
        npu['y'] = torch.tensor([2**-8,2,2**-8,.5]).to(dtype)
        row = self.compare(gpu, npu, update=scale)['first_exceedance']
        self.assertEqual((row['first_exceedance'],row['first_values']), ([2],{'gpu':0.,'npu':.03125}))
        partial = self.compare(gpu, npu, update=lambda req: scale(req, True))
        self.assertEqual(partial['result'], 'insufficient_evidence')
        self.assertIn('fp8_scale', partial['coverage']['npu']['missing'][0]['reason'])

    def test_extreme_fp8_scales_preserve_decision_beyond_fp64_arithmetic(self):
        self.call['slots'] = [self.call['slots'][-1]]
        self.policy = {}
        gpu = {'y':torch.tensor([0, 120]).to(torch.float8_e4m3fn)}
        npu = {'y':torch.tensor([.5,120]).to(torch.float8_e4m3fn)}
        def scaled(request):
            for side in ('gpu', 'npu'):
                path = Path(request[side]['path'])
                bundle = json.loads(path.read_text())
                bundle['tensors'][0]['fp8_scale'] = {'block_shape':[1], 'values':[5e-324, 1e308]}
                request[side] = write(path, bundle)
        failed = self.compare(gpu, npu, scaled)
        self.assertEqual(failed['target_status'], 'fail')
        self.assertEqual(failed['first_exceedance']['first_exceedance'], [0])
        self.assertEqual(self.compare(gpu, gpu, scaled)['target_status'], 'pass')

    def test_fp8_e5m2_scalar_and_block_scale_cross_chunk_boundary(self):
        self.call.pop('cpu_reference')
        self.call['slots'] = [self.call['slots'][-1]]
        self.policy = {'rtol':0}
        unit = 2**-16
        gpu = {'y':torch.zeros(65537).to(torch.float8_e5m2)}
        def scaled(request):
            for side in ('gpu','npu'):
                path = Path(request[side]['path'])
                bundle = json.loads(path.read_text())
                scale = {'block_shape':[65536], 'values':[1,8]} if side == 'gpu' else 4
                bundle['tensors'][0]['fp8_scale'] = scale
                request[side] = write(path, bundle)
        raw = torch.zeros(65537)
        raw[-1] = 2*unit
        report = self.compare(gpu, {'y':raw.to(torch.float8_e5m2)}, scaled)
        self.assertEqual(report['target_status'], 'pass')
        raw[-1] = 3*unit
        row = self.compare(gpu, {'y':raw.to(torch.float8_e5m2)}, scaled)['first_exceedance']
        self.assertEqual(row['first_exceedance'], [65536])
        self.assertEqual(row['first_values'], {'gpu':0., 'npu':12*unit})

    def test_provenance_and_partial_cpu_output_coverage(self):
        other = dict(self.call['slots'][-1], id='other', gpu=['output',1], npu=['output',1])
        self.call['slots'].append(other)
        data = {**self.example(), 'other':torch.tensor([17])}
        def relabel(request):
            for side in ('gpu','npu'):
                path = Path(request[side]['path'])
                body = json.loads(path.read_text())
                body['evidence_kind'] = 'runtime' if side == 'gpu' else 'synthetic'
                request[side] = write(path, body)
        for update in (None, relabel):
            report = self.compare(data, data, update)
            self.assertEqual((report['result'], report['target_status']), ('aligned','pass'))
            self.assertNotIn('hardware_target_status', report)
            self.assertEqual(report['calls'][0]['cpu_check']['unassessed_outputs'], ['other'])

    def test_partial_coverage_does_not_hide_numeric_failure(self):
        data = self.example()
        self.policy = {}
        report = self.compare(data, {'y':data['y'] + 1})
        self.assertEqual((report['result'],report['target_status']), ('mismatch','fail'))
        self.assertEqual(report['first_exceedance']['slot'], 'y')
        self.assertEqual({s['slot'] for s in report['coverage']['npu']['missing']}, {'x','w'})

    def test_nonfinite_integer_and_masked_range(self):
        data = self.example()
        gold = {**data, 'y': torch.tensor([[10, 11]], dtype=torch.int64)}
        test = {**data, 'y': torch.tensor([[10, 12]], dtype=torch.int64)}
        self.policy = {'atol': 100, 'rtol': 100}
        self.assertEqual(self.compare(gold, test)['result'], 'mismatch')
        transformed = self.call['slots'][-1]
        transformed['transforms'] = {s: [{'slice': {'axis': 1, 'start': 0, 'stop': 1}}] for s in ('gpu', 'npu')}
        changed = {**data, 'y': torch.tensor([[float('inf'), float('nan')]])}
        self.assertEqual(self.compare(changed, changed)['result'], 'mismatch')
        transformed['allow_matching_inf'] = True
        self.assertEqual(self.compare(changed, changed)['result'], 'aligned')
        transformed.pop('transforms')
        report = self.compare(changed, changed)
        self.assertEqual(report['first_exceedance']['first_exceedance'], [0, 1])
        self.assertEqual(report['first_exceedance']['first_values']['gpu'], 'nan')

    def test_gpu_is_explicit_reference_and_shape_dtype_are_structural(self):
        data = self.example()
        g = {**data,'y':torch.tensor([[1.]])}
        n = {**data,'y':torch.tensor([[2.]])}
        self.policy = {'atol': 0, 'rtol': .6}
        row = self.compare(g, n)
        self.assertEqual(row['result'], 'mismatch')
        self.assertEqual(row['first_exceedance']['first_values'], {'gpu':1.,'npu':2.})
        self.assertEqual(row['calls'][0]['roles']['output']['result'], 'fail')
        self.assertEqual(self.compare(n, g)['result'], 'aligned')
        for changed in (torch.ones(2), torch.ones((1,1), dtype=torch.float64)):
            row = self.compare(g, {**g, 'y':changed})
            self.assertEqual(row['result'], 'mismatch')
            self.assertEqual(row['first_exceedance']['criterion'], 'structure')

    def test_float16_bfloat16_layout_and_sha_mismatch(self):
        self.call.pop('cpu_reference')
        data = self.example()
        x = torch.arange(6).reshape(2, 3).to(torch.bfloat16)
        gpu, npu = {**data, 'x': x}, {**data, 'x': x.T.contiguous()}
        self.call['slots'][0]['transforms'] = {'npu': [{'permute': [1, 0]}]}
        self.assertEqual(self.compare(gpu, npu)['result'], 'aligned')

        def corrupt(request):
            package = Path(request['gpu']['path'])
            tensor = json.loads(package.read_text())['tensors'][0]
            (package.parent / tensor['path']).write_bytes(b'changed')
        report = self.compare(gpu, npu, update=corrupt)
        self.assertEqual(report['result'], 'insufficient_evidence')
        self.assertEqual(report['coverage']['gpu']['verified_slots'], 2)

    def test_earliest_by_actual_invocation_and_greatest_error(self):
        self.call['occurrence'] = 1
        early = deepcopy(self.call)
        early.update(id='early', occurrence=0)
        self.plan['calls'].append(early)
        data = self.example()
        gpu = {(c, k, o): v for c,o in [('early',2),('block.linear',6)] for k,v in data.items()}
        npu = dict(gpu)
        npu['early','y',2] = data['y'] + .25
        npu['block.linear','y',6] = data['y'] + 4
        report = self.compare(gpu, npu)
        self.assertEqual(report['first_exceedance']['call'], 'early')
        self.assertEqual(report['largest_error']['call'], 'block.linear')

    def test_missing_both_sides_and_synthetic_identity(self):
        report = self.compare({}, {})
        self.assertEqual(report['evidence_kind'], {'gpu':'synthetic','npu':'synthetic'})
        self.assertEqual(report['result'], 'insufficient_evidence')
        self.assertIsNone(report['first_exceedance'])

    def test_narrow_view_cannot_carry_full_unselected_storage(self):
        data = self.example()
        data['x'] = torch.ones(128, 2)[5:6]
        report = self.compare(data)
        self.assertEqual(report['capture_status'], 'incomplete')
        self.assertIn('存储', report['coverage']['gpu']['missing'][0]['reason'])

    def test_capture_slice_verified_against_pre_slice_shape(self):
        data = self.example()
        self.call['slots'][0]['capture_slice'] = {'axis':0,'start':0,'stop':1}
        self.assertEqual(self.compare(data, data)['capture_status'], 'incomplete')

        def bind(request):
            for side in ('gpu', 'npu'):
                package = Path(request[side]['path'])
                body = json.loads(package.read_text())
                body['tensors'][0].update(capture_slice=self.call['slots'][0]['capture_slice'], original_shape=[2,2])
                request[side] = write(package, body)
        report = self.compare(data, data, update=bind)
        self.assertEqual(report['capture_status'], 'complete')
        self.assertEqual(report['calls'][0]['cpu_check']['result'], 'not_evaluated')

    def test_path_escape_is_rejected_even_if_bytes_match(self):
        data = self.example()

        def escape(request):
            file = Path(request['gpu']['path'])
            payload = json.loads(file.read_text())
            tensor = payload['tensors'][0]
            tensor['path'] = '../' + file.parent.name + '/' + tensor['path']
            request['gpu'] = write(file, payload)

        report = self.compare(data, update=escape)
        self.assertEqual(report['capture_status'], 'incomplete')

    def test_runtime_needs_worker_frame_and_exclusive_files(self):
        versions = {'torch':'2.x.test','vllm':'1.test','msprobe':'2.5.test'}
        self.plan['runtime_versions'] = {'gpu':versions, 'npu':versions}

        def as_runtime(request, index=False, unexpected=False, drift=False):
            package = Path(request['gpu']['path'])
            data = json.loads(package.read_text())
            data.update(evidence_kind='runtime', trace_id='trace')
            if index:
                for row, slot in zip(data['tensors'], self.call['slots'], strict=True):
                    row.update(native_name='linear.0.forward', native_parent='layer', argument=slot['gpu'], occurrence=0)
                execution = {'trace_id': 'trace', 'internal_id':'internal', 'output_index': 1,
                             'phase':'decode', 'positions':[1], 'input_ids':[22], 'logits_indices':[0],
                             'rank':0,'mode':'eager','synchronous':True,'batch_size':1,'world_size':1,
                             'versions':{**versions, 'vllm':'changed'} if drift else versions}
                path = package.parent / 'native-index.json'
                native = {k:data[k] for k in ('hardware','plan_sha256','forward_id','rank','tensors')}
                data['native_index'] = {**write(path, {**native,'execution':execution,'format':'tensor-native-index-v1'}),
                                        'path':path.name}
            if unexpected:
                torch.save(torch.zeros(3), package.parent / 'unexpected.pt')
            request['gpu'] = write(package, data)

        data = self.example()
        report = self.compare(data, update=as_runtime)
        self.assertEqual(report['capture_status'], 'complete')
        report = self.compare(data, update=lambda req: as_runtime(req, True))
        self.assertEqual(report['capture_status'], 'complete')
        report = self.compare(data, update=lambda req: as_runtime(req, True, True))
        self.assertEqual(report['capture_status'], 'incomplete')
        report = self.compare(data, update=lambda req: as_runtime(req, True, drift=True))
        self.assertEqual(report['capture_status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
