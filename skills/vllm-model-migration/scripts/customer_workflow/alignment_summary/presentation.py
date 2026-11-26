"""用同一组逐题记录生成机器计数和客户首屏。"""
from collections import Counter

from .token import DIFFERENCES

LABEL = {'not_requested': '本次未要求', 'not_triggered': '未触发', 'not_run': '尚未执行',
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


def case_gap(row):
    if bucket(row) in ('insufficient_evidence', 'input_mismatch'):
        return row.get('reason') or '缺少可判定的逐题比较或容差'
    if row.get('capture_status') == 'incomplete' or row.get('scope_uncovered'):
        return '部分计划槽位缺失或调用尚未配对'
    if any(call['unassessed'] for call in row.get('calls', [])):
        return '部分槽位尚无判定'
    return None


def location(item):
    return ' '.join(str(item[k]) for k in ('dataset', 'subset', 'sample_id') if item.get(k) is not None)


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


def diagnostic_text(layer, result, coverage, gaps):
    text = [layer.name + '：' + LABEL[result]]
    if coverage['expected'] is None:
        text.append('完整范围待定')
    if layer.cases:
        totals = coverage['by_result']
        text.append(f"已定界 {len(layer.cases)} 题，对齐 {totals['aligned']}，差异 {totals['mismatch']}，"
                    f"输入不可比 {totals['input_mismatch']}，缺证 {totals['insufficient_evidence']}")
    for kind, tally in coverage['by_evidence_kind'].items():
        if kind == 'synthetic':
            text.append(f"模拟对照一致 {tally['aligned']} 题、模拟差异 {tally['mismatch']} 题")
    differences = [row for row in layer.cases if bucket(row) == 'mismatch']
    if differences:
        row = next((r for r in differences if r.get('evidence_kind') == 'runtime'), differences[0])
        text.append(location(row) + ' ' + finding(row))
    if gaps:
        gap = gaps[0]
        text.append(f"共 {len(gaps)} 项缺口，{location(gap)} {gap['reason']}".strip())
    if result == 'not_triggered':
        text.append('已核实上游无适用的异常项')
    return '，'.join(text) + '。'


class Layer:
    def __init__(self, name):
        self.name = name
        self.cases = []
        self.gaps = []
        self.reports = []

    def gap(self, scope, reason, **details):
        self.gaps.append({**scope, **details, 'reason': reason})

    def finish(self, inactive=None, *, direct=False):
        gaps = list(self.gaps)
        for row in self.cases:
            reason = case_gap(row)
            if reason:
                gaps.append({**{k: row[k] for k in ('dataset', 'subset', 'sample_id', 'quality_report')}, 'reason': reason})
        by_kind = counts(self.cases)
        totals = Counter({'aligned': 0, 'mismatch': 0, 'input_mismatch': 0, 'insufficient_evidence': 0})
        totals.update(bucket(row) for row in self.cases)
        unknown = bool(self.gaps) or inactive in ('not_run', 'not_requested', 'insufficient_evidence')
        coverage = {'expected': None if unknown else len(self.cases), 'known': len(self.cases),
                    'by_result': totals, 'by_evidence_kind': by_kind}
        result = inactive or (direct_verdict(totals, gaps) if direct else verdict(by_kind, bool(gaps)))
        summary = diagnostic_text(self, result, coverage, gaps)
        return {'result': result, 'summary': summary, 'coverage': coverage, 'cases': self.cases,
                'gaps': gaps, 'reports': self.reports}
