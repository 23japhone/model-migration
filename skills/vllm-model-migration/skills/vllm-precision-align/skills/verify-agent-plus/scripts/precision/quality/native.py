"""重读整次运行的原生报告、预测和 reviews，不发起新推理或重新评分。"""
from pathlib import Path

from ..evidence import decode, load, sha256
from ..results import coverage
from ..runtime.evalscope.binding import check_snapshot
from ..runtime.evalscope.results import records, link_reviews
from .projection import scores


def active_stats(count):
    return {'requested': count, 'succeeded': count, 'errored': 0}


def verify_report(report, tasks):
    first = tasks[0]
    if (report['schema_version'] != 2 or report['dataset_name'] != first['benchmark']
            or report['model_name'] != first['model_abbr'] or report.get('judge_summary')):
        raise ValueError('原生报告的工具结构、模型、数据或规则评分来源不符')
    sizes = {task['subset']: active_stats(len(task['sample_ids'])) for task in tasks}
    summary = report['execution_summary']
    total = active_stats(sum(row['requested'] for row in sizes.values()))
    if (summary.get('subsets') != sizes or summary.get('incomplete') is not False
            or any(type(summary.get(k)) is not int or summary[k] != n for k,n in total.items())):
        raise ValueError('原生 benchmark 执行不完整或实际覆盖与请求不同')


def quality_reviews(path):
    result = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = decode(line)
            index = row['index']
            sample = row['sample_score']
            if type(index) is not int or type(sample['sample_id']) is not int or index != sample['sample_id']:
                raise ValueError('原生评审记录的样本 ID 不一致')
            grade = sample['score']
            if grade.get('status') != 'success' or grade.get('judge_summary'):
                raise ValueError('原生规则评分器未成功完成')
            if not isinstance(grade.get('value', {}), dict):
                raise ValueError('原生逐题评分对象无效')
            result.append({**row, 'id': index})
    return result


def read_subset(native, task, stable, files):
    abbr = task['model_abbr']
    stem = f"{task['benchmark']}_{task['subset']}.jsonl"
    paths = [native / part / abbr / stem for part in ('predictions', 'reviews')]
    errors = []
    predicted = records(paths[0], 'predictions', task, errors)
    reviewed = quality_reviews(paths[1])
    for rows in (predicted, reviewed):
        coverage(rows, task, errors)
    link_reviews(predicted, reviewed, errors)
    if errors:
        raise ValueError('；'.join(errors))
    if len(stable) != len(task['sample_ids']) or len(stable) != len(set(stable)):
        raise ValueError('稳定样本 ID 集合未与原文件一一对应')
    check_snapshot(task, predicted, reviewed, files)
    files.update({str(p): sha256(p) for p in paths})
    by_id = dict(zip(task['sample_ids'], stable))
    values = [(by_id[r['id']], r['sample_score']['score'].get('value', {})) for r in reviewed]
    return values


def measure(root, tasks, stable, specs):
    root = Path(root)
    native = root / 'evalscope'
    first = tasks[0]
    report_path = native / 'reports' / first['model_abbr'] / (first['benchmark'] + '.json')
    report = load(report_path)
    verify_report(report, tasks)
    files = {str(report_path): sha256(report_path)}
    reviews = {}
    for task in tasks:
        subset = task['subset']
        reviews[subset] = read_subset(native, task, stable[subset], files)
    return {'scores': scores(report, reviews, specs), 'files': files,
            'formal_requests': report['execution_summary']['requested'],
            'request_count_source': 'evalscope.execution_summary.requested'}
