"""用同一组逐题记录生成机器计数和客户首屏。"""
from collections import Counter

from .token import DIFFERENCES

LABEL = {'not_requested': '本次未要求', 'not_triggered': '未触发', 'not_run': '尚未执行', 'pending': '任务执行中，待汇总',
         'aligned': '本次范围内已对齐', 'mismatch': '已发现差异', 'simulated': '模拟对照',
         'insufficient_evidence': '证据不足'}


def bucket(row):
    if row['result'] in DIFFERENCES:
        return 'mismatch'
    if row['result'] in ('aligned', 'input_mismatch'):
        return row['result']
    return 'insufficient_evidence'


def counts(cases):
    by_kind = {}
    for row in cases:
        by_kind.setdefault(row.get('evidence_kind', 'unspecified'), Counter())[bucket(row)] += 1
    return by_kind


def verdict(by_kind, incomplete):
    runtime = by_kind.get('runtime', {})
    if runtime.get('mismatch'):
        return 'mismatch'
    if by_kind.get('synthetic', {}).get('mismatch'):
        return 'simulated'
    if incomplete or 'unspecified' in by_kind:
        return 'insufficient_evidence'
    if 'synthetic' in by_kind:
        return 'simulated'
    return 'aligned' if runtime else 'not_triggered'


def direct_verdict(totals, incomplete):
    if totals['mismatch']:
        return 'mismatch'
    if incomplete:
        return 'insufficient_evidence'
    return 'aligned' if totals['aligned'] else 'not_triggered'


def case_gap(row, direct):
    if bucket(row) in ('insufficient_evidence', 'input_mismatch'):
        return row.get('reason') or '缺少可判定的逐题比较或容差'
    if not direct and row.get('evidence_kind') == 'unspecified':
        return 'GPU/NPU 双端运行或模拟来源未核实'
    unassessed = [f"{call['call']}.{slot}" for call in row.get('calls', []) for slot in call['unassessed']]
    if unassessed:
        return '尚无判定的槽位 ' + '、'.join(unassessed)
    if row.get('capture_status') == 'incomplete' or row.get('scope_uncovered'):
        return '部分计划槽位缺失或调用尚未配对'
    return None


def location(item):
    scope = '/'.join(str(item[k]) for k in ('dataset', 'subset', 'metric') if item.get(k) is not None)
    return f"{scope} 题号 {item['sample_id']}" if 'sample_id' in item else scope


def finding(row):
    prefix = {'synthetic': '模拟', 'unspecified': '来源未确认的'}.get(row.get('evidence_kind'), '')
    if 'token_report' in row and 'position' not in row:
        return prefix + str(row.get('reason', '预测出现差异'))
    first = row.get('first_exceedance') or {}
    where = f"第 {row['position']} 个输出位置"
    if first:
        return f"{prefix}{where}，调用 {first['call']} 的 {first['slot']} 首先超限"
    failures = [f"{c['call']}.{slot}" for c in row.get('calls', []) for slot in c['failed']]
    return f"{prefix}{where}，已确认超限槽位 " + '、'.join(failures)


def gap_text(gaps):
    examples = gaps[:2]
    if len(examples) == 2 and all('sample_id' not in gap for gap in examples):
        example = next((gap for gap in gaps if 'sample_id' in gap), None)
        if example:
            examples[1] = example
    details = '；'.join((location(gap) + ' ' + str(gap['reason'])).strip() for gap in examples)
    return f'共 {len(gaps)} 项缺口，{details}'


def coverage_text(coverage):
    parts = [f"已定界 {coverage['known']} 题"]
    labels = {'aligned': '对齐', 'mismatch': '差异', 'input_mismatch': '输入不可比', 'insufficient_evidence': '缺证'}
    for kind, tally in coverage['by_evidence_kind'].items():
        for outcome, count in tally.items():
            label = labels[outcome]
            if kind == 'synthetic':
                label = '模拟对照一致' if outcome == 'aligned' else '模拟' + label
            elif kind == 'unspecified' and outcome in ('aligned', 'mismatch'):
                label = '来源未确认的' + label
            parts.append(f'{label} {count} 题')
    return '，'.join(parts)


def diagnostic_text(layer, result, coverage, gaps):
    text = [layer.name + '：' + LABEL[result]]
    if coverage['expected'] is None and result not in ('not_requested', 'not_run', 'pending'):
        text.append('完整范围待定')
    if layer.cases:
        text.append(coverage_text(coverage))
    differences = [row for row in layer.cases if bucket(row) == 'mismatch']
    if differences:
        row = next((r for r in differences if r.get('evidence_kind') == 'runtime'), differences[0])
        text.append(location(row) + ' ' + finding(row))
    if gaps:
        text.append(gap_text(gaps))
    if result == 'not_triggered':
        text.append(layer.trigger_reason)
    return '，'.join(text) + '。'


class Layer:
    def __init__(self, name, trigger_reason):
        self.name = name
        self.trigger_reason = trigger_reason
        self.cases = []
        self.gaps = []
        self.reports = []

    def gap(self, scope, reason, **details):
        self.gaps.append({**scope, **details, 'reason': reason})

    def finish(self, inactive=None, *, direct=False):
        gaps = list(self.gaps)
        for row in self.cases:
            reason = case_gap(row, direct)
            if reason:
                gaps.append({**{k: row[k] for k in ('dataset', 'subset', 'sample_id', 'quality_report')}, 'reason': reason})
        by_kind = counts(self.cases)
        totals = Counter({'aligned': 0, 'mismatch': 0, 'input_mismatch': 0, 'insufficient_evidence': 0})
        totals.update(bucket(row) for row in self.cases)
        unknown = bool(self.gaps) or inactive is not None
        coverage = {'expected': None if unknown else len(self.cases), 'known': len(self.cases),
                    'cases_with_gaps': len(gaps) - len(self.gaps), 'by_result': totals, 'by_evidence_kind': by_kind}
        result = inactive or (direct_verdict(totals, gaps) if direct else verdict(by_kind, bool(gaps)))
        summary = diagnostic_text(self, result, coverage, gaps)
        return {'result': result, 'summary': summary, 'coverage': coverage, 'cases': self.cases,
                'gaps': gaps, 'reports': self.reports}
