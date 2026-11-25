"""核对公开归档原有路径与摘要后，将固定文件重定位到本地搜索目录。"""
from pathlib import PurePosixPath
import re

from coordination.runtime import sources
from ...state import identity
from ..resolution import publish


def locate(tree, index, key):
    """支持可移植相对路径，以及保留原运行绝对路径的镜像。"""
    old = PurePosixPath(key)
    if '..' in old.parts or not old.name or '\\' in key:
        raise ValueError('GPU 归档附件路径无效')
    folder = PurePosixPath(index).parent
    if not old.is_absolute():
        relative = (folder / old).as_posix()
        if relative in tree.files:
            return relative
        if key in tree.files:
            return key
    names = [file for file in tree.files if PurePosixPath(file).name == old.name]
    if len(names) > 1:
        names = [file for file in names if old.as_posix().endswith('/' + file)
                 or PurePosixPath(file).parent == folder]
    if len(names) != 1:
        raise ValueError('不能唯一定位 GPU 原始文件：' + old.name)
    return names[0]


def verified(search, tree, index, ref):
    if not isinstance(ref, dict) or not re.fullmatch(r'[0-9a-f]{64}', ref.get('sha256', '')):
        raise ValueError('GPU 归档附件缺少 SHA256')
    url = tree.url(locate(tree, index, ref['path']))
    search.get(url)
    local = search.sources[url]['payload']
    if local['sha256'] != ref['sha256']:
        raise ValueError('GPU 已发布附件与原始摘要不符：' + url)
    return local


def index_from_run(file, row, source_ref):
    if row.get('hardware') != 'gpu' or row.get('status') != 'completed' or not row.get('protocol'):
        return None
    files = row['files']
    dataset = row['protocol']['dataset']
    config = [k for k in files if 'config' in PurePosixPath(k).name.lower() and k.endswith('.json')]
    report = [k for k in files if 'reports' in PurePosixPath(k).parts and
              PurePosixPath(k).name == dataset + '.json']
    if len(config) != 1 or len(report) != 1:
        raise ValueError('GPU 同轮文件清单未唯一指向原生 TaskConfig 和评分报告')
    return {'format': 'evalscope-gpu-history-v1', 'source': {'path': file, 'sha256': source_ref['sha256']},
            'config': {'path': config[0], 'sha256': files[config[0]]},
            'report': {'path': report[0], 'sha256': files[report[0]]}}


def candidate(search, tree, file, data):
    url = tree.url(file)
    original = search.sources[url]['payload']
    if data.get('format') == 'evalscope-gpu-history-v1':
        index = data
    elif data.get('task_id') == 'verify-agent-plus' and data.get('evidence', {}).get('gpu_history'):
        ref = data['evidence']['gpu_history']
        attached = verified(search, tree, file, ref)
        file = locate(tree, file, ref['path'])
        index = sources.bound(attached)
        if index.get('format') != 'evalscope-gpu-history-v1':
            raise ValueError('GPU 测量回执指向的历史归档格式无法识别')
    else:
        index = index_from_run(file, data, original)
        if not index:
            if data.get('schema_version') == 2 and data.get('metrics'):
                raise ValueError('已有 EvalScope 原生报告，缺少可绑定的同轮 GPU 设备、权重、配置和样本来源')
            return None
    return relocate(search, tree, file, index, original)


def relocate(search, tree, file, index, original):
    source = verified(search, tree, file, index['source'])
    record = sources.bound(source)
    folder = search.root / 'archives' / identity([tree.url(file), original['sha256']])
    local = {'format': 'evalscope-gpu-history-v1', 'published_source': source}
    bound_files = {}
    for role in ('config', 'report', 'static_conditions', 'runtime', 'scoring_proof', 'adapter'):
        if role not in index and role not in ('config', 'report'):
            continue
        ref = index[role]
        if record['files'].get(ref['path']) != ref['sha256']:
            raise ValueError('GPU 原始运行清单未绑定附件：' + role)
        item = verified(search, tree, file, ref)
        local[role] = item
        bound_files[item['path']] = item['sha256']
    local['source'] = publish(folder / 'source.json', {**record, 'files': bound_files})
    # 保存原始索引、运行清单与来源，供相对路径重定位后的历史依据核查。
    publish(folder / 'published.json', {'url': tree.url(file), 'original': original, 'source': source, 'localized': local})
    return publish(folder / 'index.json', local)
