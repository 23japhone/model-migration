"""生成显式 synthetic 的 GPU/NPU 证据，走公开 Token 与 Tensor CLI；输出放在新目录。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
STAGE = ROOT / 'skills/vllm-model-migration/skills/vllm-precision-align/scripts/precision.py'
CASE = {'subset': 'math', 'sample_id': 2}
PROTOCOL = {'dataset': 'fixture', 'model_identity': hashlib.sha256(b'synthetic-two-vector').hexdigest(),
            'generation_config': {'temperature': 0}}


def bind(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def put(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return bind(path)


def get(src):
    return json.loads(Path(src['path']).read_bytes())


def stage(root, action, label, request):
    path = root / (label + '.request.json')
    put(path, request)
    command = [sys.executable, '-B', str(STAGE), '--action', action, '--request', str(path),
               '--work-root', str(root / 'runs'), '--run-id', 'synthetic']
    proc = subprocess.run(command, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(proc.stdout + proc.stderr)
    return json.loads(proc.stdout)


def quality_fixture(root):
    measurement = put(root / 'native-npu.json', {'hardware':'npu', 'format':'task-quality-native-v1',
                      'protocol':PROTOCOL, 'evidence_kind':'synthetic', 'scores':[]})
    native = put(root / 'quality-receipt.json', {'task_id':'verify-agent-plus', 'operation':'quality-run',
                 'status':'completed', 'conclusions':{'evaluation_validity':'pass'},
                 'detailed_report':{'hardware':'npu'},'evidence':{'quality_evidence':measurement}})
    score = {'subset':'math', 'metric':'accuracy', 'direction':'higher', 'precision_target':'fail',
             'comparability':'pass', 'unit':'ratio', 'npu_score':0, 'gpu_score':1,
             'npu_minus_gpu':-1, 'drop':1, 'target':{'max_drop':0}, 'sample_comparison':{'status':'completed',
             'items':[{**CASE, 'gpu_score':1, 'npu_score':0}]}}
    return put(root / 'quality.json', {'task_id':'vllm-precision-compare','operation':'run','status':'completed',
               'conclusions':{'precision_target':'fail','evaluation_validity':'pass'},'scores':[score],
               'baseline':{'dataset':'fixture'},'evidence':{'receipt':native,'measurement':measurement},
               'next_action':{'requires_separate_run':True,
                              'failed_scores':[{'dataset':'fixture','metric':'accuracy','subset':'math'}]}})


def token_bundle(root, quality, side):
    logits = [[0,2], [0,2], [0,2]] if side == 'gpu' else [[0,2], [0.5,2.5], [2,0]]
    low = 1 / (1 + math.exp(2))
    probs = [[low,1-low] for _ in logits]
    if side == 'npu':
        probs[-1].reverse()
    accepted = [max(range(len(row)), key=row.__getitem__) for row in logits]
    prediction = {**CASE, 'evidence_kind':'synthetic', 'prompt_token_ids':[1,0], 'output_token_ids':accepted,
                  'raw_logits':logits, 'processed_probs':probs, 'stop_reason':'length'}
    record = put(root / ('token-'+side+'.json'), prediction)
    review = put(root / ('review-'+side+'.json'), {**CASE, 'hardware':side, 'evidence_kind':'synthetic',
                 'quality_report_sha256':quality['sha256'], 'record_sha256':record['sha256'],
                 'metrics':{'accuracy':int(side == 'gpu')}})
    return put(root / ('token-'+side+'-bundle.json'), {'format':'token-prediction-bundle-v1','hardware':side,
               'quality_report_sha256':quality['sha256'], 'evidence_kind':'synthetic',
               'cases':[{**CASE, 'record':record, 'review':review}]})


def common_scope(root):
    op = {'owner':'model.layers.0', 'name':'Torch.add', 'scalar_args':{}}
    slot = lambda name, role, path: {'id':name, 'role':role, 'gpu':path, 'npu':path}
    call = {'id':'add0', 'kind':'api', 'occurrence':0, 'gpu':op, 'npu':op,
            'slots':[slot('a','input',['input_args',0]), slot('b','input',['input_args',1]),
                     slot('y','output',['output',0])]}
    return put(root / 'scope.json', {'format':'tensor-scope-v1','model_identity':PROTOCOL['model_identity'],
                                     'calls':[call]})


def tensor_bundle(folder, plan_ref, side, perturb):
    import torch
    folder.mkdir()
    plan = get(plan_ref)
    a = torch.tensor([[1.,2.],[3.,4.]])
    b = torch.tensor([[1.,1.],[1.,1.]])
    y = a + b
    y[1,0] += perturb
    rows = []
    for name, array in [('a',a),('b',b),('y',y)]:
        file = folder / (name + '.pt')
        torch.save(array, file)
        rows.append({'call':'add0', 'slot':name, 'ordinal':0,'path':file.name,'sha256':bind(file)['sha256'],
                     'shape':list(array.shape), 'dtype':'float32','layout':'strided'})
    metadata = {k:plan[k] for k in ('case','prefix_token_ids','prompt_token_ids','position','phase','rank','model_identity')}
    record = get(plan['predictions'][side])
    ix = plan['position']-1
    prediction = {'raw_logits':record['raw_logits'][ix], 'processed_probs':record['processed_probs'][ix],
                  'token_id':record['output_token_ids'][ix]}
    return put(folder / 'bundle.json', {**metadata,'format':'tensor-bundle-v1','hardware':side,
               'evidence_kind':'synthetic', 'forward_id':side+'-synthetic-2', 'plan_sha256':plan_ref['sha256'],
               'tensors':rows, 'prediction':prediction})


def generate(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    quality = quality_fixture(root)
    bundles = [token_bundle(root, quality, side) for side in ('gpu','npu')]
    token = stage(root, 'token', 'second-pass', {'schema_version':'1.0','quality_report':quality,'bundles':bundles,
                 'token_alignment':{'raw_logits':{'atol':0},'processed_scores':{'atol':0}}})
    req = {'schema_version':'1.0','quality_report':quality,'token_report':token['token_report'],
           'tensor_alignment':{'atol':0,'rtol':0}, 'evidence':[common_scope(root)]}
    waiting = stage(root, 'tensor', '01-request-gpu', req)
    plan = waiting['plans'][0]
    gpu = tensor_bundle(root / 'gpu', plan, 'gpu', 0)
    npu = tensor_bundle(root / 'npu', plan, 'npu', 0)
    changed = tensor_bundle(root / 'gpu-with-outlier', plan, 'gpu', 4)
    measurements = {}
    for label, inputs, atol in [('02-gpu-only',[plan,gpu],0), ('03-aligned',[plan,gpu,npu],0),
                                ('04-gpu-outlier',[plan,changed,npu],0), ('05-relaxed',[plan,changed,npu],4)]:
        measured = stage(root, 'tensor', label, {**req,'evidence':inputs, 'tensor_alignment':{'atol':atol,'rtol':0}})
        measurements[label] = measured
    result = {'format':'synthetic-tensor-stage-demonstration-v1', 'second_pass':token, 'waiting':waiting,
              'runs':measurements, 'plan':plan}
    return put(root / 'fixture-result.json', result)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', required=True, type=Path, help='用于保存所有证据的新目录')
    args = cli.parse_args()
    print(json.dumps(generate(args.output), ensure_ascii=False))
