"""核对公开资产声明与请求一致，不重复资产内容检查。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
import json
from pathlib import Path, PurePosixPath
import re

from .results import identity


def matching(asset, request, fields):
    for field in fields:
        if field not in asset or asset[field] != request.get(field):
            raise ValueError('资产声明与本次请求不一致：'+field)


def content_path(name, asset, request):
    value = asset.get('path')
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('资产 path 必须为有效的绝对路径')
    path = Path(value)
    if not path.is_absolute() or not path.exists() or path.resolve() != path:
        raise ValueError('资产路径缺失、未规范化或通过符号链接重定向')
    expected = Path(request['path']).resolve()
    if name == 'model':
        valid = path == expected
    elif expected.is_file():
        valid = path == expected.parent
    else:
        valid = path.is_relative_to(expected)
    if not valid:
        raise ValueError('资产位置不属于本次请求指定的路径')
    actual = asset.get('identity')
    if not isinstance(actual, dict) or actual.get('format') != f'vllm-{name}-file-list-v1':
        raise ValueError('资产缺少有效的内容身份格式')
    if not re.fullmatch(r'[a-f0-9]{64}', str(actual.get('sha256', ''))):
        raise ValueError('资产内容身份缺少有效 SHA256')
    if request.get('expected_identity') and actual != request['expected_identity']:
        raise ValueError('资产内容身份与本次 expected_identity 不一致')


def repository(reference):
    value = reference.split('@', 1)[0]
    head, tail = value.rsplit('/', 1) if '/' in value else ('', value)
    return (head+'/' if head else '')+tail.split(':', 1)[0]


def image_contract(asset, request):
    actual = asset.get('image_id')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(actual)):
        raise ValueError('镜像清单缺少有效的实际 image ID')
    if request.get('expected_image_id') and actual != request['expected_image_id']:
        raise ValueError('镜像 image ID 与本次请求不一致')
    matching(asset, request, ('recipe', 'hardware'))
    platform = asset.get('platform')
    wanted = request['platform']
    if platform != wanted and (platform, wanted) not in (('linux/arm64', 'linux/arm64/v8'), ('linux/arm64/v8', 'linux/arm64')):
        raise ValueError('镜像 platform 与本次请求不一致')
    daemon_contract(asset.get('daemon'), wanted)
    image_source(asset, request)


def daemon_contract(daemon, platform):
    if not isinstance(daemon, dict) or any(not isinstance(daemon.get(k), str) or not daemon[k].strip()
                                          for k in ('Name', 'ID', 'OSType', 'Architecture', 'ServerVersion')):
        raise ValueError('镜像清单缺少实际 Docker daemon 位置与版本')
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(daemon['Architecture'], daemon['Architecture'])
    if daemon['OSType'] != 'linux' or arch != platform.split('/')[1]:
        raise ValueError('Docker daemon 平台与本次请求不一致')


def image_source(asset, request):
    source = request['recipe']['source_ref']
    if asset.get('source_ref') != source:
        raise ValueError('镜像来源声明与本次配方不一致')
    digests = asset.get('repo_digests')
    if not isinstance(digests, list) or any(not isinstance(x, str) for x in digests):
        raise ValueError('镜像清单的 RepoDigests 无效')
    reference = request.get('reference', source)
    if reference.startswith('sha256:'):
        reference = source
    if request.get('expected_digest') and repository(reference)+'@'+request['expected_digest'] not in digests:
        raise ValueError('镜像 repository digest 与本次请求不一致')
    origins = [d for d in digests if d.startswith(repository(source)+'@sha256:')]
    if '@' in source and origins and source not in origins:
        raise ValueError('镜像来源 digest 与固定配方不一致')
    expected = 'registry_record' if origins else 'declared_recipe'
    if asset.get('source_status') != expected:
        raise ValueError('镜像来源证明声明与 RepoDigests 不一致')


def dataset_contract(asset, request):
    matching(asset, request, ('recipe', 'format'))
    recipes = json.loads((release_skill('vllm-dataset-prepare')/'references/recipes.json').read_text(encoding='utf-8'))
    splits = recipes.get(request['recipe'], {}).get('splits', [request['split']])
    if asset.get('required_splits') != splits:
        raise ValueError('数据集 required_splits 与公开配方不一致')
    files = dataset_files(asset.get('files'), splits)
    selection = asset.get('selection')
    if not isinstance(selection, dict):
        raise ValueError('数据集缺少有效样本选择清单')
    matching(selection, request, ('split', 'subjects'))
    if selection.get('parameters') != request['selection']:
        raise ValueError('数据集样本选择参数与本次请求不一致')
    selection_records(selection, files, request)
    requested = Path(request['path'])
    if requested.is_file() and set(files) != {requested.name}:
        raise ValueError('通用单文件的数据声明与请求文件不一致')


def dataset_files(rows, splits):
    if not isinstance(rows, list) or not rows:
        raise ValueError('数据集缺少文件声明')
    files = {}
    for row in rows:
        dataset_file(row, splits)
        if row['path'] in files:
            raise ValueError('数据集文件声明重复')
        files[row['path']] = row
    if {row['split'] for row in rows} != set(splits):
        raise ValueError('数据集文件声明没有覆盖配方必需 split')
    return files


def dataset_file(row, splits):
    if not isinstance(row, dict) or not isinstance(row.get('path'), str):
        raise ValueError('数据集文件声明格式无效')
    value = row['path']
    if not value or value == '.' or '\\' in value or '\0' in value:
        raise ValueError('数据集文件名必须为非空的 POSIX 相对路径')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value:
        raise ValueError('数据集文件声明路径无效')
    if row.get('split') not in splits or type(row.get('rows')) is not int or row['rows'] < 1:
        raise ValueError('数据集文件的 split 或样本数声明无效')


def selection_records(selection, files, request):
    records = selection.get('records')
    if not isinstance(records, list) or not records or type(selection.get('count')) is not int or selection['count'] != len(records):
        raise ValueError('数据集样本数与选择记录不一致')
    if selection.get('sha256') != identity(records):
        raise ValueError('数据集样本选择摘要与声明不一致')
    if 'limit' in request['selection'] and len(records) != request['selection']['limit']:
        raise ValueError('数据集样本数与请求 limit 不一致')
    locations = []
    for record in records:
        selection_location(record, files, request)
        locations.append((record['file'], record['row_index']))
    if len(set(locations)) != len(locations):
        raise ValueError('数据集样本选择记录重复')


def selection_location(record, files, request):
    if not isinstance(record, dict) or not isinstance(record.get('file'), str) or record['file'] not in files:
        raise ValueError('数据集样本没有对应的文件声明')
    row = files[record['file']]
    index = record.get('row_index')
    if type(index) is not int or not 0 <= index < row['rows']:
        raise ValueError('数据集样本位置超出文件声明范围')
    if record.get('split') != request['split'] or record.get('split') != row['split'] or record.get('subject') != row.get('subject'):
        raise ValueError('数据集样本 split 或科目与声明不一致')
    if request['subjects'] and record.get('subject') not in request['subjects']:
        raise ValueError('数据集样本科目超出本次选择范围')


def review_asset(name, asset, inputs):
    if name == 'image':
        image_contract(asset, inputs)
        return
    content_path(name, asset, inputs)
    if name == 'model':
        matching(asset, inputs, ('scope', 'task', 'modalities'))
    else:
        dataset_contract(asset, inputs)
