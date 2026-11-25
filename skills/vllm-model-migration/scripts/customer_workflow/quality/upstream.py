"""已知数据集的发行源及原始字节映射；其他 benchmark 由 Agent 解析来源。"""
import re

from coordination.runtime import sources
from ..state import identity
from .resolution import publish
from .snapshot import MAX_BYTES, contents, official_zip, raw_url, relative

REPOSITORIES = {'ceval': 'https://www.modelscope.cn/datasets/opencompass/ceval-exam',
                'gsm8k': 'https://github.com/openai/grade-school-math'}


def ceval_rows(source, revision):
    archive_name = 'ceval-exam.zip'
    url = raw_url(source['repository'], revision, archive_name)
    with official_zip(contents(url, source)) as archive:
        entries = [f for f in archive.infolist() if not f.is_dir()]
        if sum(f.file_size for f in entries) > 128 * 1024**2:
            raise ValueError('C-Eval 发布包大小超出题库范围')
        for f in entries:
            if re.fullmatch(r'(dev|val|test)/[a-zA-Z0-9_]+_(dev|val|test)\.csv', f.filename):
                yield f.filename, f.filename, archive.read(f)


def gsm8k_rows(source, revision):
    for name in ('train.jsonl', 'test.jsonl'):
        upstream = 'grade_school_math/data/' + name
        url = raw_url(source['repository'], revision, upstream)
        yield name, upstream, contents(url, source)


def historical_rows(source, binding, revision):
    mapping = binding.get('upstream_paths', {name: name for name in binding['files']})
    if set(mapping) != set(binding['files']) or binding['repository'] != source['repository']:
        raise ValueError('通用题库的官方仓库或原文件路径映射不完整')
    for local, remote in mapping.items():
        relative(local)
        relative(remote)
    if not binding.get('archive'):
        for local, remote in mapping.items():
            yield local, remote, contents(raw_url(source['repository'], revision, remote), source)
        return
    url = raw_url(source['repository'], revision, binding['archive'])
    with official_zip(contents(url, source)) as archive:
        names = archive.namelist()
        for local, remote in mapping.items():
            if names.count(remote) != 1 or archive.getinfo(remote).file_size > MAX_BYTES:
                raise ValueError('历史官方归档缺少唯一可读取的成员：' + remote)
            yield local, remote, archive.read(remote)


def save_binding(source, selected, root, rows, archive_name):
    revision = selected['dataset_revision']
    path = root / 'official-data' / revision
    upstream, files = {}, {}
    for local, remote, data in rows:
        if local in files:
            raise ValueError('官方发布包存在重复题库路径')
        files[local] = publish(path / local, data)['sha256']
        upstream[local] = remote
    if not files:
        raise ValueError('不可变官方版本未包含原始题库')
    binding = {'repository': source['repository'], 'revision': revision, 'files': files, 'upstream_paths': upstream}
    if archive_name:
        binding['archive'] = archive_name
    ref = publish(root / 'upstream' / (identity(binding) + '-binding.json'), binding)
    publish(root / 'upstream' / (identity(binding) + '.json'),
            {'repository': source['repository'], 'revision': revision, 'files': files})
    publish(root / 'dataset-binding.json', ref)
    return ref


def choose_rows(name, source, revision, prior):
    if source['repository'] == REPOSITORIES.get(name):
        if name == 'ceval':
            return ceval_rows(source, revision), 'ceval-exam.zip', {'recipe': name}
        return gsm8k_rows(source, revision), None, {'recipe': name}
    if not prior:
        raise ValueError('Agent 需根据该 benchmark 官方仓库绑定题库快照的原始文件清单：' + name)
    preparation = {key: value for key, value in source['preparation'].items()
                   if key in ('recipe', 'format', 'required_fields', 'csv_header', 'files', 'supporting_files')}
    return historical_rows(source, prior, revision), prior.get('archive'), preparation


def bind_builtin(name, source, selected, root):
    revision = selected['dataset_revision']
    prior = sources.bound(source['snapshot_binding']) if source.get('snapshot_binding') else None
    if prior and (prior['revision'] == revision or 'public_gpu' not in selected['selection']):
        return source
    rows, archive_name, preparation = choose_rows(name, source, revision, prior)
    index = root / 'dataset-binding.json'
    if index.exists():
        ref = sources.read(index)
        binding = sources.bound(ref)
        if binding['revision'] != revision or binding['repository'] != source['repository']:
            raise ValueError('官方题库冻结来源发生变化')
    else:
        ref = save_binding(source, selected, root, rows, archive_name)
    return {**source, 'snapshot_binding': ref, 'preparation': {
        **preparation, 'source': 'local', 'path': str(root / 'official-data' / revision)}}
