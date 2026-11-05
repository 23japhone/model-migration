"""按原生指标身份解析值；显示倍率不参与精度判定。"""
import json
import math


def identity(value):
    if isinstance(value, dict) and isinstance(value.get('dimensions'), dict):
        value = {**value, 'dimensions': {key: int(item) if type(item) == float and item.is_integer() else item
                                        for key, item in value['dimensions'].items()}}
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def selected_metric(report, spec):
    selector = identity(spec['selector'])
    matches = [row for row in report.get('metrics', []) if identity(row.get('identity')) == selector]
    if len(matches) != 1:
        raise ValueError('指标 selector 未唯一匹配原生结果')
    return matches[0]


def check_semantics(metric, spec):
    semantics = metric['semantics']
    direction = {'higher': 'higher_is_better', 'lower': 'lower_is_better'}[spec['direction']]
    if semantics.get('kind') != 'quality' or semantics.get('direction') != direction:
        raise ValueError('原生指标方向或种类与请求不一致')
    unit, _ = raw_unit(semantics)
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError('原生指标缺少明确的原始单位')
    if unit != spec['unit'] and not (spec['unit'] == 'value' and unit not in ('ratio', 'percent')):
        raise ValueError('请求单位与原始指标单位不一致；不能使用显示百分比代替原始值')
    value = metric.get('score')
    if not number(value):
        raise ValueError('缺少有限原生指标')
    bounds = semantics.get('value_range')
    if bounds and not bounds['min'] <= value <= bounds['max']:
        raise ValueError('原生指标超出声明范围')
    return value


def raw_unit(semantics):
    unit = semantics.get('raw_unit')
    if unit is not None:
        return unit, 'raw_unit'
    # 原生 semantic_id 定义为 domain.concept.unit；无量纲 ratio 的 raw_unit 可为 null。
    semantic_id = semantics.get('semantic_id')
    parts = semantic_id.split('.') if isinstance(semantic_id, str) else []
    return (parts[-1], 'semantic_id') if len(parts) >= 3 and all(p.strip() for p in parts) else (None, 'undeclared')


def check_coverage(metric, task):
    count = len(task['sample_ids'])
    if type(metric.get('num')) != int or metric['num'] != count:
        raise ValueError('原生指标未覆盖所有声明样本')
    subsets = [s for c in metric.get('categories', []) for s in c.get('subsets', [])
               if not s.get('is_aggregate', False)]
    if len(subsets) != 1 or subsets[0].get('name') != task['subset'] or subsets[0].get('num') != count:
        raise ValueError('原生指标 subset 覆盖不一致')


def parse(report, task, errors):
    result = {}
    for key, spec in task['metrics'].items():
        row = {**spec, 'value': None}
        try:
            metric = selected_metric(report, spec)
            check_coverage(metric, task)
            row.update(value=check_semantics(metric, spec), native_identity=metric['identity'],
                       native_semantics=metric['semantics'], unit_source=raw_unit(metric['semantics'])[1])
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            errors.append('无有效指标 ' + key + '：' + str(error))
        result[key] = row
    return result
