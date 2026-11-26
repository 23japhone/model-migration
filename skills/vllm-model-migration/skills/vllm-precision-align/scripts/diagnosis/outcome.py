"""单题定界台账。评测结果与诊断报告各自保留独立身份。"""
from pathlib import Path

from alignment.evidence import bound, ref, safe


def available_id(rows, preferred):
    key = preferred
    suffix = 1
    while key in rows:
        suffix += 1
        key = f'{preferred}-{suffix}'
    return key


def report_reference(value, run_root, code):
    path = Path(value['report_path'])
    if path.name != 'demarcation-manifest.json' or path.parent.parent != run_root / 'three-step-demarcation':
        raise ValueError('定界回执不属于本次运行')
    passed = value['status'] == 'completed' and value['conclusions']['demarcation'] == 'pass'
    expected_code = 0 if passed else (3 if value['status'] == 'blocked' else 1)
    if value.get('success') is not passed or code != expected_code:
        raise ValueError('定界回执的退出码、状态或结论不一致')
    reference = ref(path)
    if bound(reference) != value:
        raise ValueError('定界回执与终端报告不一致')
    return reference


def record(row, invoke, args, work_root, manifest, timeout):
    row['status'] = 'running'
    try:
        work_root = Path(work_root).expanduser().absolute()
        code, value = invoke(*args, work_root, manifest['run_id'], timeout)
        if not value.get('report_path'):
            reason = value.get('diagnostic', {}).get('message') or value.get('summary')
            raise ValueError(reason or f'定界未交付报告，退出码 {code}')
        receipt = report_reference(value, work_root / manifest['run_id'], code)
        row.update(status=value['status'], receipt=receipt, conclusions=value['conclusions'],
                   uncovered=value['uncovered'])
        if value.get('diagnostic'):
            row['diagnostic'] = value['diagnostic']
    except (ValueError, OSError, TypeError, KeyError) as error:
        reason = safe(error)
        row.update(status='failed', conclusions={'demarcation': 'not_evaluated'}, reason=reason,
                   uncovered=[reason])
    manifest['uncovered'].extend(row['uncovered'])
