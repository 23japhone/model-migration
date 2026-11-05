"""从已核验原文件构造独立原生布局，不修改来源或题目内容。"""
import json
import csv
from pathlib import Path
import shutil

from .assets import file_hash, inspect_dataset


def ceval_metadata(files, root, split):
    subjects = sorted({row['subject'] for row in files if row['subject'] is not None})
    lines = ['---', 'configs:']
    for subject in subjects:
        lines.extend(['- config_name: ' + json.dumps(subject), '  keep_default_na: false', '  data_files:'])
        for row in files:
            if row['subject'] == subject and row['split'] in ('dev', split):
                lines.extend(['  - split: ' + json.dumps(row['split']),
                              '    path: ' + json.dumps(row['path'])])
        source = next(row for row in files if row['subject'] == subject and row['split'] == split)
        with (root / source['path']).open(encoding='utf-8-sig', newline='') as stream:
            columns = next(csv.reader(stream))
        lines.append('  features:')
        for column in columns:
            lines.extend(['  - name: ' + json.dumps(column), '    dtype: string'])
    lines.extend(['---', '', 'Generated local C-Eval layout; original CSV bytes are unchanged.', ''])
    return '\n'.join(lines)


def gsm8k_metadata():
    return '\n'.join([
        '---', 'configs:', '- config_name: main', '  default: true', '  data_files:',
        '  - split: train', '    path: train.jsonl',
        '  - split: test', '    path: test.jsonl', '---', '',
        'Generated local GSM8K layout; original JSONL bytes are unchanged.', '',
    ])


def copy_verified(root, destination, row):
    source = root / row['path']; target = destination / row['path']
    if source.resolve() != source or not source.is_relative_to(root):
        raise ValueError('原始数据路径发生重定向：' + row['path'])
    if source.stat().st_size != row['size'] or file_hash(source) != row['sha256']:
        raise ValueError('复制前原始数据身份变化：' + row['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as stream, target.open('xb') as output:
        shutil.copyfileobj(stream, output)
    if target.stat().st_size != row['size'] or file_hash(target) != row['sha256']:
        raise ValueError('评测副本与原始数据不一致：' + row['path'])


def prepare_asset(request, path, attempt):
    original = inspect_dataset(request, path)
    if request.get('layout', 'raw') == 'raw':
        return original
    root = Path(original['path']); destination = attempt / 'evalscope-data'
    destination.mkdir()
    for row in original['files']:
        if row['split'] is not None:
            copy_verified(root, destination, row)
    metadata = (ceval_metadata(original['files'], root, request['split'])
                if request['recipe'] == 'ceval' else gsm8k_metadata())
    (destination / 'README.md').write_text(metadata, encoding='utf-8')
    derived = {key: value for key, value in request.items() if key != 'expected_identity'}
    derived['supporting_files'] = ['README.md']
    asset = inspect_dataset(derived, destination)
    if inspect_dataset(request, path) != original:
        raise ValueError('生成评测目录期间原始数据发生变化')
    if asset['selection'] != original['selection']:
        raise ValueError('生成评测目录改变了原始样本选择')
    asset.update(layout='evalscope', provenance={'format': 'evalscope-layout-v1', 'source_root': str(root),
                 'source_identity': original['identity'], 'source_files': original['files']})
    return asset
