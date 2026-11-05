"""显式运行的 GSM8K 原生加载检查；复用已有 SDK，不下载数据或调用模型。"""
import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
DATA = ROOT / 'skills/vllm-migration-prepare/skills/vllm-dataset-prepare/scripts'
PRECISION = ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'
sys.path[:0] = [str(ROOT / 'scripts'), str(PRECISION)]

from customer_workflow.contract import normalize
from customer_workflow.compile.assets import dataset_request, subsets
from customer_workflow.compile.accuracy import request as accuracy_request
from precision.runtime.evalscope.catalog import base_config


def fixture(root):
    root.mkdir()
    rows = [{'question': f'Test question {i}: what is {i} + 1?', 'answer': f'calculation #### {i + 1}'}
            for i in range(5)]
    train = [{'question': 'Training example only', 'answer': '#### 100'}]
    for split, records in (('test', rows), ('train', train), ('test_socratic', train), ('train_socratic', train)):
        (root / (split + '.jsonl')).write_text(''.join(json.dumps(row) + '\n' for row in records))
    return rows


def native_adapter(config, root):
    from evalscope.api.registry import get_benchmark
    from precision.runtime.evalscope.configuration import native_config
    isolated = {**config, 'work_dir': str(root / 'native-work'), 'dataset_dir': str(root / 'native-data')}
    return get_benchmark('gsm8k', native_config(isolated))


def resolve(task, backend, root):
    dataset = task['accuracy']['datasets'][0]
    discovery = base_config({'model': 'fixture', 'dataset': dataset})
    if backend == 'evalscope':
        names = list(native_adapter(discovery, root).subset_list)
    else:
        # datasets-only 模式验证加载边界；main 来自 GSM8K 官方声明，不模拟 SDK 发现通过。
        names = discovery['dataset_args']['gsm8k'].get('subset_list', ['main'])
    assert len(names) == 1, names
    catalog = {'preparation': {'source': 'local', 'recipe': 'gsm8k', 'path': dataset['path']},
               'subsets': [{'name': name, 'split': 'test', 'source_file': 'test.jsonl', 'metrics': {}}
                           for name in names]}
    selected = subsets(dataset, catalog)[0]
    preparation = dataset_request(dataset, catalog, selected)
    preparation['layout'] = 'evalscope'
    return dataset, catalog, selected, preparation


class Artifacts:
    def __init__(self, root):
        self.root = root

    def artifact(self, name, value):
        path = self.root / (name + '.json')
        path.write_text(json.dumps(value))
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def prepare(task, root, backend):
    dataset, catalog, selected, preparation = resolve(task, backend, root)
    artifacts = Artifacts(root)
    request_path = artifacts.artifact('request', preparation)['path']
    process = subprocess.run([sys.executable, '-B', str(DATA / 'prepare.py'), '--request', request_path,
                              '--work-root', str(root / 'work'), '--run-id', 'gsm8k-check'],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    manifest = Path(json.loads(process.stdout)['manifest'])
    report = json.loads(manifest.read_text())
    job = {'id': 'gsm8k', 'dataset': dataset, 'subset': selected, 'catalog': catalog,
           'receipt': {'path': str(manifest), 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}}
    evaluator = {'python': sys.executable, 'version': 'unused', 'backend': 'evalscope'}
    request = accuracy_request(task, job, report, evaluator, 'http://127.0.0.1:1', artifacts, False)
    config = json.loads(Path(request['evaluator']['config']['path']).read_text())
    return report['asset'], request, config


def check_case(task, root, expected_rows, backend):
    original = copy.deepcopy(task)
    asset, request, config = prepare(task, root, backend)
    limit = task['accuracy']['datasets'][0]['limit']
    expected = expected_rows if limit is None else expected_rows[:limit]
    assert config['limit'] == limit
    assert config['dataset_args']['gsm8k']['few_shot_num'] == 0
    assert asset['selection']['count'] == len(expected)
    assert request['tasks'][0]['sample_ids'] == list(range(len(expected)))
    assert {row['file'] for row in asset['selection']['records']} == {'test.jsonl'}
    if backend == 'evalscope':
        from precision.runtime.evalscope.samples import verify
        checked = verify(native_adapter(config, root), asset, request['tasks'][0])
        assert len(checked['samples']) == len(expected)
        digest = checked['reader_samples_sha256']
    else:
        from datasets import load_dataset
        args = config['dataset_args']['gsm8k']
        name = args['subset_list'][0]
        data = load_dataset(args['local_path'], name=None if name == 'default' else name,
                            split=args['eval_split'], cache_dir=str(root / 'cache'))
        assert data.to_list() == expected_rows  # 同一原始测试文件，未混入 train/socratic。
        selected = data.to_list() if config['limit'] is None else data.to_list()[:config['limit']]
        assert selected == expected
        digest = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    assert task == original
    return len(expected), digest


def run(backend):
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import datasets
    datasets.disable_progress_bars()
    versions = {'datasets': datasets.__version__}
    if backend == 'evalscope':
        versions['evalscope'] = importlib.metadata.version('evalscope')
    results = []
    with tempfile.TemporaryDirectory(prefix='gsm8k-native-check-') as folder:
        root = Path(folder).resolve(); source = root / 'source'
        expected = fixture(source)
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        for label, limit in (('missing', {}), ('null', {'limit': None}), ('default', {'limit': -1}),
                             ('one', {'limit': 1}), ('partial', {'limit': 3}),
                             ('exact', {'limit': 5}), ('over', {'limit': 99})):
            identities = set()
            for index, choice in enumerate((['default'], ['main'], -1)):
                case = root / f'{label}-{index}'; case.mkdir()
                task = normalize({'model': {'model_name': 'fixture'}, 'environment': {},
                                  'accuracy': {'datasets': [{'name': 'gsm8k', 'path': str(source),
                                                            'subset_list': choice, **limit}]}})
                count, digest = check_case(task, case, expected, backend)
                identities.add(digest)
                results.append({'subset_list': choice, 'limit_case': label, 'count': count})
            assert len(identities) == 1, (label, identities)
        assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    print(json.dumps({'backend': backend, 'versions': versions, 'cases': results,
                      'same_samples_for_all_subsets': True, 'source_unchanged': True}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('datasets', 'evalscope'), default='datasets')
    run(parser.parse_args().backend)
