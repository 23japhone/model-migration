"""在固定评测环境读取原生子集、样本和 tokenizer 长度，不推理或评分。"""
import copy
import dataclasses
import json
from pathlib import Path
import sys
import importlib.metadata
import os

from ...evidence import sha256
from ...inputs import dataset as prepared_dataset
from .configuration import native_config
from .samples import capability, text_only, verify, check_directory
from .data import bind_config, metadata_ready


def base_config(request, asset=None):
    dataset = request['dataset']
    args = copy.deepcopy(request.get('dataset_args', {}))
    args.setdefault('few_shot_num', 0)
    if asset is None:
        args['local_path'] = dataset['path']
    if dataset['subset_list'] != -1:
        args['subset_list'] = dataset['subset_list']
    raw = {'model': request['model'], 'model_id': request['model'], 'api_url': 'http://127.0.0.1:8000/v1',
            'eval_type': 'openai_api', 'eval_backend': 'Native', 'api_key': 'EMPTY',
            'datasets': [dataset['name']], 'dataset_args': {dataset['name']: args},
            'limit': None if dataset['limit'] == -1 else dataset['limit'], 'eval_batch_size': 1,
            'generation_config': {'stream': False, 'n': 1, 'retries': 1, 'max_tokens': 8192,
                                  'temperature': 0, 'top_p': 1, 'seed': 42},
            'model_args': {'max_retries': 0}, 'judge': {'strategy': 'rule'}, 'seed': 42}
    return bind_config(raw, asset) if asset is not None else raw


def tokens(sample, tokenizer, thinking):
    text_only(sample)
    if isinstance(sample.input, str):
        messages = [{'role': 'user', 'content': sample.input}]
    else:
        messages = [{'role': m.role, 'content': m.content} for m in sample.input]
    args = {'enable_thinking': False} if thinking else {}
    token_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                             return_dict=False, return_tensors=None,
                                             padding=False, truncation=False, **args)
    if not isinstance(token_ids, list) or not token_ids or any(type(token_id) is not int for token_id in token_ids):
        raise ValueError('tokenizer 应返回单条样本的非空整数 token ID 列表，实际类型为 ' + type(token_ids).__name__)
    return len(token_ids)


def template_switch(tokenizer):
    from jinja2 import Environment, meta
    template = tokenizer.get_chat_template()
    return 'enable_thinking' in meta.find_undeclared_variables(Environment().parse(template))


def data_snapshot(path):
    root = Path(path)
    files = [root] if root.is_file() else sorted(root.rglob('*'))
    base = root.parent if root.is_file() else root
    result = {}
    for file in files:
        if file.is_symlink() or file.resolve() != file:
            raise ValueError('原生数据目录不能经过链接重定向')
        if file.is_file():
            result[file.relative_to(base).as_posix()] = sha256(file)
    if not result:
        raise ValueError('原生数据目录没有可核验文件')
    return result


def subset_samples(samples, name):
    kind = type(samples).__module__ + '.' + type(samples).__qualname__
    missing = []
    if not callable(getattr(samples, 'keys', None)):
        missing.append('keys()')
    if not callable(getattr(type(samples), '__getitem__', None)):
        missing.append('[子集名]')
    if missing:
        raise ValueError('原生 loader 返回类型 ' + kind + ' 缺少公开接口：' + '、'.join(missing))
    if set(samples.keys()) != {name} or not samples[name]:
        raise ValueError('原生 loader 没有返回唯一的非空所选子集')
    return samples[name]


def inspect(request, attempt):
    from evalscope.api.registry import get_benchmark
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    dataset = request['dataset']
    asset = prepared_dataset(request['dataset_manifest']) if 'dataset_manifest' in request else None
    raw = base_config(request, asset)
    raw.update(work_dir=str(attempt / 'native-work'), dataset_dir=str(attempt / 'native-data'))
    config = native_config(raw)
    adapter = get_benchmark(dataset['name'], config)
    capability(adapter)
    metadata = json.loads(json.dumps(dataclasses.asdict(adapter.benchmark_meta), default=str))
    module = Path(sys.modules[type(adapter).__module__].__file__).resolve()
    output = {'name': dataset['name'], 'metadata': metadata, 'adapter_source': {'path': str(module), 'sha256': sha256(module)},
              'local_path': raw['dataset_args'][dataset['name']]['local_path'],
              'subset_names': list(adapter.subset_list), 'subsets': [], 'eval_split': adapter.eval_split,
              'inputs': request, 'evaluator_version': importlib.metadata.version('evalscope')}
    if not Path(output['local_path']).exists():
        output['next_action'] = 'agent_resolve_official_dataset_source'
        output['source_resolution'] = {'requested_path': dataset['path'], 'download_path': dataset['path'],
                                       'check_default_local_first': True}
        output['next_action_detail'] = ('查官方来源；如有默认本地目录，用 default_local 传入该目录及官方资料引用，'
                                        '先核验复用。默认目录不存在才下载到 requested_path；已有坏数据不得覆盖。')
        return output
    if request.get('metadata_only') is True:
        output['next_action'] = 'prepare_dataset_then_bind_catalog'
        return output
    load_subsets(request, raw, adapter, asset, output)
    return output


def load_subsets(request, raw, adapter, asset, output):
    from evalscope.api.registry import get_benchmark
    from transformers import AutoTokenizer
    name = request['dataset']['name']
    root = output['local_path']
    if asset is not None:
        metadata_ready(name, root)
        check_directory(Path(root), Path(asset['path']), asset['files'])
        output['dataset_manifest'] = request['dataset_manifest']
    output['data_files'] = data_snapshot(root)
    tokenizer = AutoTokenizer.from_pretrained(request['model_path'], local_files_only=True, trust_remote_code=False)
    thinking = template_switch(tokenizer)
    output['thinking_supported'] = thinking
    for name in adapter.subset_list:
        single = copy.deepcopy(raw)
        benchmark = request['dataset']['name']
        single['dataset_args'][benchmark]['subset_list'] = [name]
        selected = get_benchmark(benchmark, native_config(single))
        loaded = selected.load_dataset()
        samples = subset_samples(loaded, name)
        if asset is not None:
            verified = verify(selected, asset, {'subset': name, 'sample_ids': list(range(asset['selection']['count']))}, loaded)
            output.setdefault('sample_identities', {})[name] = verified['reader_samples_sha256']
        lengths = [tokens(sample, tokenizer, thinking) for sample in samples]
        output['subsets'].append({'name': name, 'split': selected.eval_split,
                                  'count': len(lengths), 'max_prompt_tokens': max(lengths)})
    if output['data_files'] != data_snapshot(root):
        raise ValueError('原生数据在 token 长度发现期间变化')
    if asset is not None and asset != prepared_dataset(request['dataset_manifest']):
        raise ValueError('准备回执在原生目录发现期间变化')
    output['next_action'] = 'agent_bind_original_source_files_and_native_metrics'
