"""核验精度阶段公开结果投影和聚合关系；不加载评分器或重算测量。"""
from .sources import bound


def precision(value, check_child):
    groups = value['tasks']
    if set(groups) != {'datasets','deployments','evaluations'} or not all(isinstance(g, dict) for g in groups.values()):
        raise ValueError('精度任务台账缺失')
    expected = {item['id']: item for item in value['inputs']['evaluations']}
    if not expected or set(expected) != set(groups['evaluations']):
        raise ValueError('评测台账与请求范围不一致')
    for group, skill in [('datasets','vllm-dataset-prepare'), ('deployments','verify-agent'), ('evaluations','verify-agent-plus')]:
        if group != 'evaluations' and set(groups[group]) != set(value['inputs'].get(group, {})):
            raise ValueError('精度依赖台账越出请求范围')
        for row in groups[group].values():
            check_child(row, skill, value['run_id'])
    results = value.get('results', {})
    if not isinstance(results, dict) or set(results) - set(expected):
        raise ValueError('精度结果范围无效')
    for key, item in expected.items():
        raw = precision_input(item, groups['evaluations'][key], groups, value.get('operation'))
        if key in results:
            projection(results[key], groups['evaluations'][key], raw)
    observations(value)
    conclusions(value)


def observations(value):
    resources = value.get('resources', [])
    if not isinstance(resources, list) or any(not isinstance(r, dict) for r in resources):
        raise ValueError('精度资源观察须为对象列表')


def precision_input(item, row, groups, action):
    if not row.get('receipt'):
        return None
    raw = bound(row['receipt'])
    expected = bound(item['request'])
    for field, group in (('dataset', 'datasets'), ('deployment', 'deployments')):
        if field in item:
            expected[field] = groups[group][item[field]]['receipt']
    if raw['inputs'] != expected or raw.get('operation') != action:
        raise ValueError('原始精度任务输入或操作不符')
    measurement_details(raw)
    declarations(expected, row, raw)
    if row.get('status') == 'completed' and raw['conclusions'].get('evaluation_validity') != 'pass':
        raise ValueError('完成任务缺少有效测量结论')
    return raw


def measurement_details(raw):
    details = raw.get('detailed_report', {})
    rows = details.get('measurements', [])
    if not isinstance(rows, list) or not isinstance(details.get('conditions'), (dict, type(None))):
        raise ValueError('原始精度测量列表或条件结构无效')
    if any(not isinstance(row, dict) or type(row.get('valid')) is not bool
           or not isinstance(row.get('metrics'), dict) for row in rows):
        raise ValueError('原始精度测量条目结构无效')
    if raw['status'] == 'completed' and (not rows or any(not row['valid'] or not row['metrics'] for row in rows)):
        raise ValueError('完成精度任务缺少非空有效测量及指标')


def projection(result, row, raw):
    if raw is None or not isinstance(result, dict):
        raise ValueError('精度结果缺少原始回执')
    expected = {'receipt': row['receipt'], 'conclusions': raw['conclusions'],
                'measurements': raw.get('detailed_report', {}).get('measurements', []),
                'comparison': raw.get('detailed_report', {}).get('comparison'),
                'conditions': raw.get('detailed_report', {}).get('conditions'), 'uncovered': raw['uncovered']}
    defaults = {'measurements': [], 'comparison': None, 'conditions': None, 'uncovered': []}
    if any(result.get(k, defaults.get(k)) != v for k,v in expected.items()):
        raise ValueError('精度结果未绑定原始任务测量、比较或结论')


def verdict(values):
    if 'fail' in values:
        return 'fail'
    if 'review_required' in values:
        return 'review_required'
    return 'pass' if values and all(v == 'pass' for v in values) else 'not_evaluated'


def conclusions(value):
    rows = value['tasks']['evaluations']
    if value['status'] == 'completed':
        if set(value['results']) != set(rows) or any(r['status'] != 'completed' for r in rows.values()):
            raise ValueError('精度伪 completed')
        if any(value['conclusions'].get(k) != 'pass' for k in ('precision_testing','evaluation_validity')):
            raise ValueError('精度 completed 缺少有效测量')
    for field, flag in [('precision_target','targets_requested'), ('comparability','baseline_requested')]:
        values = [r['conclusions'][field] for key,r in value['results'].items()
                  if rows[key].get(flag) or r['conclusions'][field] != 'not_evaluated']
        expected = verdict(values)
        if expected == 'pass' and value['status'] != 'completed':
            expected = 'not_evaluated'
        if value['conclusions'].get(field) != expected:
            raise ValueError('阶段目标或可比结论与原始任务聚合不一致')


def declarations(expected, row, raw):
    for field, flag, conclusion in [('targets','targets_requested','precision_target'),
                                    ('baseline','baseline_requested','comparability')]:
        requested = bool(expected.get(field))
        if bool(row.get(flag)) != requested:
            raise ValueError('精度目标或基线声明与输入不一致')
        if not requested and raw['conclusions'].get(conclusion) != 'not_evaluated':
            raise ValueError('缺少目标或基线时不得给出已评估结论')
