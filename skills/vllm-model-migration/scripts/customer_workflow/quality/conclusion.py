"""把已绑定的任务质量判定投影到客户回执，保留指标单位与评测范围。"""
from decimal import Decimal

from coordination.runtime import sources


TASK_STATUS = {'running': '任务执行中', 'completed': '任务已完成', 'blocked': '任务受阻塞',
               'failed': '任务执行失败', 'not_run': '任务未执行'}
SCORE_FIELDS = ('subset', 'metric', 'comparability', 'precision_target', 'gpu_score', 'npu_score',
                'unit', 'direction', 'npu_minus_gpu', 'drop', 'target', 'target_source', 'target_evidence', 'reasons')


def checked_report(entry, steps):
    if not entry.get('report'):
        diagnostic = steps.get(entry.get('step'), {}).get('diagnostic') or {}
        raise ValueError(diagnostic.get('message') or '缺少本次任务质量报告')
    report = sources.bound(entry['report'])
    if entry.get('stage') and sources.bound(entry['stage']).get('quality_report') != entry['report']:
        raise ValueError('质量报告与阶段回执引用不符')
    return report


def measurements(report):
    if (report.get('task_id'), report.get('operation'), report.get('status')) != (
            'vllm-precision-compare', 'run', 'completed'):
        raise ValueError('缺少已完成的原生质量比较报告')
    if report['conclusions'].get('evaluation_validity') != 'pass':
        raise ValueError('质量报告缺少有效测量')
    outcome = report['conclusions']['precision_target']
    if outcome not in ('pass', 'fail', 'not_evaluated') or not report.get('scores'):
        raise ValueError('质量报告缺少有效目标结论或评分项')
    scores = [project_score(row) for row in report['scores']]
    if outcome in ('pass', 'fail'):
        targets = {row['precision_target'] for row in scores if 'target' in row}
        if outcome not in targets:
            raise ValueError('质量报告缺少对应的目标评分项')
        if outcome == 'pass' and targets != {'pass'}:
            raise ValueError('质量报告的通过结论未覆盖全部约定目标')
    result = {'precision_target': outcome, 'scores': scores}
    if 'comparison_basis' in report:
        result['comparison_basis'] = report['comparison_basis']
    return result


def reasons(values):
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError('质量报告的缺项说明格式无效')
    return values


def report_dataset(report, expected):
    dataset = report.get('baseline', {}).get('dataset') or report.get('dataset')
    if dataset is None:
        return expected
    if not isinstance(dataset, str) or expected not in (None, dataset):
        raise ValueError('质量报告的数据集与客户记录不一致')
    return dataset


def project(entry, steps):
    item = {'dataset': None, 'precision_target': 'not_evaluated', 'scores': []}
    try:
        if isinstance(entry.get('dataset'), str):
            item['dataset'] = entry['dataset']
        if entry.get('report'):
            item['report'] = entry['report']
        report = checked_report(entry, steps)
        item['dataset'] = report_dataset(report, item['dataset'])
        if report.get('quality_measurement_started') is False:
            item['missing'] = reasons(report.get('missing') or report.get('uncovered') or ['缺少可比基线或评测条件'])
        else:
            item.update(measurements(report))
    except (*sources.ERRORS, AttributeError) as error:
        item['missing'] = [str(error)]
    return item


def check_differences(row):
    if row.get('comparability') == 'pass':
        if not isinstance(row.get('unit'), str) or not all(type(row.get(key)) in (int, float)
                                                         for key in ('drop', 'npu_minus_gpu')):
            raise ValueError('可比指标缺少原生单位或分差')
    if row['precision_target'] in ('pass', 'fail'):
        if row['comparability'] != 'pass' or type(row['target']['max_drop']) not in (int, float):
            raise ValueError('达标结论缺少可比指标或允许下降量')


def project_score(row):
    if not isinstance(row, dict) or not isinstance(row.get('metric'), str):
        raise ValueError('质量报告缺少指标名称')
    if row['precision_target'] not in ('pass', 'fail', 'not_evaluated'):
        raise ValueError('质量报告含未知评分项目标状态')
    reasons(row.get('reasons', []))
    check_differences(row)
    return {key: row[key] for key in SCORE_FIELDS if key in row}


def amount(value, unit, *, delta=True, exact=False):
    number = Decimal(str(value) if exact else format(value, '.15g'))
    if unit == 'ratio':
        return f'{(number * 100).normalize():f}' + (' 个百分点' if delta else '%')
    return f'{number.normalize():f} {unit}'.strip()


def describe(score):
    label = f"{score.get('subset') or '总分'} {score['metric']}"
    unit = score.get('unit', '')
    if score['precision_target'] == 'fail':
        drop, limit = score['drop'], score['target']['max_drop']
        actual, allowed = amount(drop, unit), amount(limit, unit)
        if actual == allowed and drop != limit:
            actual, allowed = amount(drop, unit, exact=True), amount(limit, unit, exact=True)
        return f'{label} 下降 {actual}，允许下降 {allowed}'
    if score['precision_target'] == 'pass':
        return label + ' 已达标'
    if score.get('comparability') == 'pass' and 'target' not in score:
        delta = amount(score['npu_minus_gpu'], unit)
        return f'{label} 未设阈值，NPU 相对 GPU 分差 {delta}'
    notes = score.get('reasons') or ['缺少可比证据或目标']
    measured = score.get('npu_score')
    npu = '，NPU 得分 ' + amount(measured, unit, delta=False) if type(measured) in (int, float) else ''
    return f"{label} 无法判定（{'、'.join(notes)}）{npu}"


def only_differences(item):
    return bool(item['scores']) and all(row.get('comparability') == 'pass' and 'target' not in row
                                        for row in item['scores'])


def verdict(reports, requested):
    if not reports:
        if requested is False:
            return 'not_requested', '本次未要求任务质量对齐'
        reason = '尚未确认客户精度意图' if requested is None else '未取得本次任务质量报告'
        return 'not_evaluated', '任务质量达标情况无法判定，' + reason
    states = {item['precision_target'] for item in reports if not only_differences(item)}
    if 'fail' in states:
        return 'fail', '任务质量未达标'
    if states == {'pass'}:
        return 'pass', '本次约定的任务质量目标均已达标'
    if not states:
        return 'not_evaluated', '已取得分差，尚无达标判据'
    return 'not_evaluated', '任务质量达标情况无法判定'


def complete_entries(entries, baselines):
    # 旧回执可能只记录已完成项。同一数据集允许按不同基线协议多次声明。
    if not isinstance(entries, list):
        entries = [{}]
    recorded = [(entry.get('step', f'quality-{i}'), entry.get('dataset'))
                for i, entry in enumerate(entries) if isinstance(entry, dict)]
    result = list(entries)
    for i, baseline in enumerate(baselines):
        key, dataset = f'quality-{i}', baseline['dataset']
        if (key, dataset) not in recorded:
            result.append({'step': key, 'dataset': dataset})
    return result


def summarize(status, entries, steps, requested, baselines=()):
    reports = [project(entry, steps) for entry in complete_entries(entries, baselines)]
    target, headline = verdict(reports, requested)
    lines = [f'{TASK_STATUS[status]}，{headline}']
    for item in reports:
        details = item.get('missing') or [describe(row) for row in item['scores']]
        basis = '，按客户提供的基线比较' if item.get('comparison_basis') == 'customer_provided' else ''
        lines.append((item['dataset'] or 'GPU 基线') + basis + '，' + '。'.join(details))
    return {'precision_target': target, 'summary': '。'.join(lines) + '。', 'reports': reports}
