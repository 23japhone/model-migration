"""把已核验且明确失败的质量分项映射到稳定的原题标识。"""
from sys import float_info


def case_key(item):
    if not isinstance(item, dict):
        raise ValueError('样本条目须为对象')
    subset = item.get('subset')
    identifier = item['sample_id']
    if not isinstance(subset, str) or not subset:
        raise ValueError('样本缺少具体子集')
    if type(identifier) not in (str, int) or identifier == '':
        raise ValueError('缺少有效稳定题号')
    return subset, identifier


def failing_rows(report):
    target = report.get('conclusions', {}).get('precision_target')
    if target not in ('pass', 'fail', 'not_evaluated'):
        raise ValueError('来源未给出任务质量结论')
    if target == 'not_evaluated' and report.get('quality_measurement_started') is False:
        return []
    if (report.get('task_id'), report.get('operation'), report.get('status')) != (
            'vllm-precision-compare', 'run', 'completed'):
        raise ValueError('质量来源不是已完成的任务质量报告')
    if target != 'fail':
        return []
    if report['conclusions'].get('evaluation_validity') != 'pass':
        raise ValueError('首轮测量无效')
    return checked_failed_rows(report)


def checked_failed_rows(report):
    rows = [row for row in report['scores'] if row.get('precision_target') == 'fail']
    dataset = report['baseline']['dataset']
    declared = report['next_action']
    expected = {(dataset, row['subset'], row['metric']) for row in rows}
    actual = {(row['dataset'], row['subset'], row['metric']) for row in declared['failed_scores']}
    if not rows or actual != expected or declared.get('requires_separate_run') is not True:
        raise ValueError('质量交接与未达标项不符')
    if any(row.get('comparability') != 'pass' or row.get('direction') not in ('higher', 'lower') for row in rows):
        raise ValueError('质量失败项不可比或指标方向未知')
    return rows


def degraded(gpu, npu, direction):
    if any(type(value) not in (bool, int, float) or not -float_info.max <= value <= float_info.max for value in (gpu, npu)):
        raise ValueError('逐题评分须为有限数值')
    return npu < gpu if direction == 'higher' else npu > gpu


def reported_pairs(row):
    samples = row.get('sample_comparison') or {}
    if samples.get('status') not in ('completed', 'partial'):
        return {}
    result = {}
    for item in samples['items']:
        key = case_key(item)
        if key in result or row['subset'] not in (None, key[0]):
            raise ValueError('首轮逐题记录重复或与分项不匹配')
        result[key] = (item['gpu_score'], item['npu_score'])
    return result


def cases_for_score(native, row):
    pairs = native.pairs(row)
    for key, scores in reported_pairs(row).items():
        if key in pairs and pairs[key] != scores:
            raise ValueError('质量报告与原生逐题分数冲突')
        pairs[key] = scores
    cases = [key for key, scores in pairs.items() if degraded(*scores, row['direction'])]
    if not cases and row['subset'] is not None:
        return [(row['subset'], identifier) for identifier in native.roster(row['subset'])], 'failed_subset_roster'
    return cases, 'regressed_sample'


def select_rows(rows, native, quality_ref, dataset):
    indexed = {}
    pending = []
    coverage = []
    for row in rows:
        scope = {'metric': row['metric'], 'subset': row['subset']}
        try:
            cases, source = cases_for_score(native, row)
        except (ValueError, KeyError, TypeError) as error:
            pending.append({**scope, 'reason': str(error)})
            continue
        if not cases:
            pending.append({**scope, 'reason': '未能定界到退化题或取得失败子集完整原生名册'})
        coverage.append({**scope, 'selection_basis': source, 'selected_count': len(cases)})
        for key in cases:
            case = indexed.setdefault(key, {'subset': key[0], 'sample_id': key[1], 'triggers': []})
            case['triggers'].append({**scope, 'direction': row['direction'], 'selection_basis': source})
    return {'format': 'token-case-selection-v1', 'quality_report': quality_ref, 'dataset': dataset,
            'cases': list(indexed.values()), 'scope_coverage': coverage, 'pending_scopes': pending, 'uncovered': native.issues}
