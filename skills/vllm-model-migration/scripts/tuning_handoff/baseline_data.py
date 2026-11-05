"""只读核对基线数据文件和选择身份，不重跑 loader 或评分。"""
import hashlib
import json
from pathlib import Path

from coordination.runtime import sources
from .contracts import require


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def checked(raw):
    receipt = sources.bound(raw['inputs']['dataset'])
    require(receipt.get('task_id') == 'vllm-dataset-prepare' and receipt.get('status') == 'completed'
            and receipt.get('conclusions', {}).get('assets') == 'pass', '基线数据准备未通过')
    asset = receipt['asset']
    check_files(asset)
    check_selection(asset)
    conditions = raw['detailed_report']['conditions']
    require(conditions.get('dataset') == asset['identity'] and conditions.get('selection') == asset['selection']['sha256'],
            '实际基线条件没有绑定数据身份')
    return asset


def check_files(asset):
    root = Path(asset['path'])
    require(root.is_absolute() and root.resolve() == root, '基线数据根无效')
    files = asset['files']
    require(files and len({row['path'] for row in files}) == len(files), '基线数据文件清单为空或重复')
    for row in files:
        path = root/row['path']
        require(not Path(row['path']).is_absolute() and '..' not in Path(row['path']).parts
                and path.resolve() == path, '基线数据文件越界或重定向')
        require(sources.ref(path)['sha256'] == row['sha256'] and path.stat().st_size == row['size'],
                '基线数据在测量后发生变化')
    content = [{key: row[key] for key in ('path', 'size', 'sha256')}
               for row in sorted(files, key=lambda row: row['path'])]
    require(asset['identity'] == {'format': 'vllm-dataset-file-list-v1', 'sha256': digest(content)},
            '基线数据内容身份无效')


def check_selection(asset):
    selected = asset['selection']
    records = selected['records']
    require(records and selected['count'] == len(records) and selected['sha256'] == digest(records),
            '基线样本选择摘要不一致')
    allowed = {row['path'] for row in asset['files']}
    require(all(row['file'] in allowed and type(row['row_index']) is int for row in records),
            '基线样本选择引用无效')
    require(len({row['file'] for row in records}) == 1
            and [row['row_index'] for row in records] == list(range(len(records))),
            '基线样本选择不能由下游 limit 表达')


def sample_range(config, task, asset):
    selected = asset['selection']
    source = next(row for row in asset['files'] if row['path'] == selected['records'][0]['file'])
    rows = source.get('rows')
    require(type(rows) is int and rows > 0, '原始数据清单缺少有效行数，无法核对全量样本范围')
    limit = config.get('limit')
    require(limit is None or (type(limit) is int and limit > 0), '冻结 limit 须为正整数或 null')
    count = rows if limit is None else min(limit, rows)
    require(selected['count'] == count and task['sample_ids'] == list(range(count)),
            '冻结配置与实际样本选择不一致')


def local_path(config, task, asset):
    args = config['dataset_args'][task['benchmark']]
    path = Path(args['local_path'])
    root = Path(asset['path'])
    require(path.is_absolute() and path.resolve() == path and path.is_relative_to(root),
            '冻结 local_path 不属于基线数据根')
    selected = asset['selection']
    sample_range(config, task, asset)
    require(args.get('eval_split') == selected['split'],
            '冻结配置与实际样本选择不一致')
    allowed = {root/row['path'] for row in asset['files']}
    actual = {p for p in path.rglob('*') if p.is_file()} if path.is_dir() else {path}
    require(actual and actual <= allowed, 'local_path 含未经基线固定的数据文件')
