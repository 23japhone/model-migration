"""在客户回执内核对各层公开的 JSON 引用，逐题隔离无法核验的证据。"""
from coordination.runtime import sources


ERRORS = (*sources.ERRORS, AttributeError)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def records(value):
    require(isinstance(value, list), '报告缺少记录列表')
    return value


def case_key(row):
    subset, sample = row['subset'], row['sample_id']
    require(isinstance(subset, str) and subset and type(sample) in (str, int) and sample != '', '子集或稳定题号无效')
    return subset, sample


def indexed(rows, layer, scope):
    index = {}
    for row in records(rows):
        try:
            key = case_key(row)
        except ERRORS as exc:
            layer.gap(scope, str(exc))
            continue
        if key in index:
            layer.gap(scope, f'{key} 题号重复')
            index[key] = None
        else:
            index[key] = row
    return index


def one_entry(manifest, key, origin):
    rows = records(manifest.get(key, []))
    matches = [row for row in rows if isinstance(row, dict) and row.get('quality_report') == origin]
    require(len(matches) == 1, '缺少唯一绑定到本次质量报告的阶段记录')
    return matches[0]


def stage_report(manifest, entry, operation):
    field = operation + '_report'
    require(entry.get(field), entry.get('reason') or f'缺少 {operation} 报告')
    report = sources.bound(entry[field])
    require(report.get('task_id') == 'vllm-precision-align' and report.get('operation') == operation,
            '阶段报告身份不符')
    require(report.get('quality_report') == entry['quality_report'], '阶段报告属于另一份质量评测')
    if entry.get('stage'):
        stage = sources.bound(entry['stage'])
        require((stage.get('task_id'), stage.get('operation'), stage['inputs'].get('quality_report')) ==
                ('vllm-precision-align', operation, entry['quality_report']), '阶段回执身份或质量来源不符')
        require((stage.get('run_id'), stage.get('status'), stage.get(field)) ==
                (manifest['run_id'], 'completed', entry[field]), '阶段回执与本次运行或报告不符')
        require(report.get('run_id', stage['run_id']) == stage['run_id'], '阶段报告运行身份不符')
    return report


def comparison(row, operation):
    report = sources.bound(row['comparison'])
    require((report.get('task_id'), report.get('operation'), report.get('status'), report.get('result')) ==
            ('vllm-precision-compare', operation, 'completed', row['result']), '逐题比较来源或结果不一致')
    return report


def evidence_kind(sides):
    kinds = [sides.get(side) for side in ('gpu', 'npu')]
    if 'synthetic' in kinds:
        return 'synthetic'
    return 'runtime' if kinds == ['runtime', 'runtime'] else 'unspecified'


def fields(row, keys):
    return {key: row[key] for key in keys if key in row}
