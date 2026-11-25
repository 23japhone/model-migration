"""从同一份原生报告和逐题 reviews 读取数据集及子集成绩，不重新聚合。"""
from ..runtime.evalscope.metrics import check_semantics, number, selected_metric

PROJECTION_ERRORS = (ValueError, KeyError, TypeError)


def subset_metric(metric, name, count):
    matches = [row for category in metric.get('categories', []) for row in category.get('subsets', [])
               if not row.get('is_aggregate') and row.get('name') == name]
    if not matches or any(row != matches[0] for row in matches):
        raise ValueError('缺少唯一的原生子集成绩：' + name)
    first = matches[0]
    if type(first.get('num')) is not int or first['num'] != count:
        raise ValueError('原生子集分数的样本数不符：' + name)
    return first


def aggregate(metric, reviews):
    expected = sum(len(rows) for rows in reviews.values())
    if type(metric.get('num')) is not int or metric['num'] != expected:
        raise ValueError('原生总分的样本数不符')
    names = {s['name'] for c in metric.get('categories', []) for s in c.get('subsets', [])
             if not s.get('is_aggregate')}
    if names != set(reviews):
        raise ValueError('原生总分的科目覆盖不同')
    for name, rows in reviews.items():
        subset_metric(metric, name, len(rows))
    return metric


def sample_score(values, spec):
    value = values.get(spec['sample_metric'])
    if spec['sample_kind'] == 'binary':
        if type(value) is bool or number(value) and value in (0, 1):
            return int(value)
        raise ValueError('原生逐题结果缺少声明的二元指标')
    if not number(value):
        raise ValueError('原生逐题结果缺少声明的数值指标')
    return value


def read_score(report, reviews, spec):
    result = {key: spec[key] for key in ('metric', 'subset', 'unit', 'direction', 'sample_kind')}
    result.update(value=None, samples=[], aggregation=spec['selector']['aggregation'])
    wanted = spec['subset']
    try:
        metric = selected_metric(report, spec)
        scoped = aggregate(metric, reviews) if wanted is None else subset_metric(metric, wanted, len(reviews[wanted]))
        result['value'] = check_semantics({**metric, 'score': scoped['score']}, spec)
    except PROJECTION_ERRORS as error:
        result['error'] = str(error)
    for subset, rows in reviews.items():
        if wanted is not None and wanted != subset:
            continue
        for identifier, values in rows:
            entry = {'subset': subset, 'sample_id': identifier}
            try:
                result['samples'].append({**entry, 'score': sample_score(values, spec)})
            except PROJECTION_ERRORS as error:
                result.setdefault('sample_errors', []).append({**entry, 'error': str(error)})
    return result


def scores(report, reviews, specs):
    return [read_score(report, reviews, spec) for spec in specs]
