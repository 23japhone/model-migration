"""通过原生 loader 和转换函数核对数据身份，不重新实现 prompt 或评分。"""
import copy
from pathlib import Path

from ...inputs import canonical
from ..sample_mapping import source_rows
from .configuration import validate_limit


def local_source(adapter, asset):
    root = Path(asset['path']); path = Path(adapter.dataset_id)
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('EvalScope local_path 必须位于已交接的本地数据根内')
    selected = asset['selection']; files = {row['file'] for row in selected['records']}
    if len(files) != 1:
        raise ValueError('当前任务须有可映射的单一原文件')
    original = next(iter(files))
    if path.is_file() and path != root / original:
        raise ValueError('local_path 与选择清单文件不同')
    check_directory(path, root, asset['files'])
    if adapter.eval_split != selected['split']:
        raise ValueError('EvalScope eval_split 与准备选择不一致')
    return next(row for row in asset['files'] if row['path'] == original)


def check_directory(path, root, files):
    if not path.exists():
        raise ValueError('local_path 不存在，不自动下载数据')
    if path.is_dir():
        allowed = {root / row['path'] for row in files}
        for item in path.rglob('*'):
            if item.is_symlink() or (item.is_file() and item not in allowed):
                raise ValueError('dataset_unlisted_file: local_path 目录含未交接或重定向的文件：' + str(item))


def capability(adapter):
    from evalscope.api.benchmark import DefaultDataAdapter
    if not isinstance(adapter, DefaultDataAdapter):
        raise ValueError('首版仅支持标准文本 DataAdapter')
    if adapter.reformat_subset or adapter.split_as_subset or adapter.shuffle:
        raise ValueError('首版不支持重新分组或打乱样本')
    adapter.validate_judge_strategy()


def text_only(sample):
    if any(getattr(sample, key) for key in ('tools', 'sandbox', 'files', 'setup')):
        raise ValueError('样本含未支持的工具或执行资源')
    if isinstance(sample.input, str):
        return
    for message in sample.input:
        if message.role not in ('system', 'user', 'assistant') or not isinstance(message.content, str):
            raise ValueError('首版只支持文本消息数据')


def sample_identity(sample):
    value = sample.model_dump(mode='json', exclude={'id', 'group_id'})
    if isinstance(value['input'], list):
        for message in value['input']:
            message.pop('id', None)  # 原生 ChatMessage 自动生成随机 ID，不参与样本内容比较。
    return value


def verify(adapter, asset, task, loaded=None):
    capability(adapter)
    source = local_source(adapter, asset)
    originals = source_rows(asset, source)
    selected = [row['row_index'] for row in asset['selection']['records']]
    validate_limit(adapter.limit)
    count = len(originals) if adapter.limit is None else min(adapter.limit, len(originals))
    if selected != list(range(count)):
        raise ValueError('dataset_sample_mismatch: limit 与原文件样本范围不一致：省略或 null 须为全量，正整数须为前 N 行上限')
    datasets = adapter.load_dataset() if loaded is None else loaded
    if set(datasets.keys()) != {task['subset']}:
        raise ValueError('dataset_sample_mismatch: 实际加载 subset 与任务不同：' + task['subset'])
    actual = datasets[task['subset']]
    if len(actual) != len(selected) or task['sample_ids'] != list(range(len(actual))):
        raise ValueError('dataset_sample_mismatch: 实际样本覆盖与请求不同：' + task['subset'])
    observed = []
    with adapter._temporary_attribute('current_subset_name', task['subset']):
        for index, original_index in enumerate(selected):
            expected = adapter.record_to_sample(copy.deepcopy(originals[original_index]))
            text_only(expected)
            if isinstance(expected.input, str):
                expected.input = adapter.process_sample_str_input(expected, task['subset'])
            else:
                expected.input = adapter.process_sample_messages_input(expected, task['subset'])
            text_only(actual[index])
            if sample_identity(expected) != sample_identity(actual[index]) or actual[index].id != index:
                raise ValueError('dataset_sample_mismatch: 原生 loader 内容与声明原始行不一致：' + task['subset'] + '/' + str(index))
            observed.append(sample_identity(actual[index]))
    return {'reader_samples_sha256': canonical(observed), 'samples': observed}
