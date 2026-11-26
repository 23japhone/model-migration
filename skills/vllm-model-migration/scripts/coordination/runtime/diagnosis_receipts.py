"""核验普通精度阶段的单题定界来源、报告投影与汇总结论。"""
from pathlib import Path

from .continuation import deployment_reference
from .receipts import common
from .sources import bound


def check_report(row, stage):
    source = row['receipt']
    path = Path(source['path'])
    root = Path(stage['evidence']['manifest']).parents[2] / 'three-step-demarcation'
    if path.parent.parent != root or path.name != 'demarcation-manifest.json':
        raise ValueError('定界报告不属于本阶段运行目录')
    raw = bound(source)
    common(raw, 'three-step-demarcation')
    identity = {'report_path': str(path), 'run_id': stage['run_id'], 'operation': 'run'}
    if any(raw.get(field) != expected for field, expected in identity.items()):
        raise ValueError('单题定界报告身份不符')
    if set(raw['conclusions']) != {'demarcation'}:
        raise ValueError('报告缺少单题定界结论')
    passed = raw['status'] == 'completed' and raw['conclusions']['demarcation'] == 'pass'
    if raw.get('success') is not passed:
        raise ValueError('单题定界报告状态与结论不符')
    if any(raw[field] != row.get(field) for field in ('status', 'conclusions', 'uncovered')):
        raise ValueError('单题定界台账与报告不符')
    return raw


def origin(row, key, explicit, stage):
    if key in explicit:
        return explicit[key], None
    if stage['inputs'].get('auto_diagnose') is False:
        raise ValueError('已关闭自动定界却有额外的诊断项')
    anomalies = stage['results'][row['evaluation']]['anomalies']
    index = row['anomaly_index']
    if type(index) is not int or not 0 <= index < len(anomalies):
        raise ValueError('定界项缺少对应的原始评测异常')
    return None, anomalies[index]


def check_input(raw, source, anomaly, stage):
    inputs = raw['inputs']
    if anomaly is not None:
        if inputs['service'] != anomaly['service'] or bound(inputs['badcase']) != anomaly['request']:
            raise ValueError('定界报告与评测异常样本不符')
        return
    deployment = bound(stage['tasks']['deployments'][source['deployment']]['receipt'])
    handoff = bound(deployment_reference(deployment))
    service = {'endpoint': handoff['endpoint'], 'model': handoff['model']['name']}
    if inputs['badcase'] != source['badcase'] or inputs['service'] != service:
        raise ValueError('定界报告与指定的异常请求或服务不符')


def diagnosis(row, source, anomaly, stage):
    if row.get('status') not in ('completed', 'failed', 'blocked', 'cancelled'):
        raise ValueError('定界台账缺少终态')
    outcome = row.get('conclusions', {}).get('demarcation', 'not_evaluated')
    if not row.get('receipt'):
        if (row['status'] == 'completed' or outcome != 'not_evaluated'
                or not (row.get('reason') or row.get('diagnostic'))):
            raise ValueError('定界项缺少报告或缺项原因')
        return outcome
    raw = check_report(row, stage)
    check_input(raw, source, anomaly, stage)
    return outcome


def check_coverage(automatic, stage):
    expected = {(key, index) for key, result in stage['results'].items()
                for index in range(len(result.get('anomalies', [])))}
    if stage['inputs'].get('auto_diagnose') is False:
        expected.clear()
    if set(automatic) != expected or len(automatic) != len(expected):
        raise ValueError('自动定界台账与评测异常范围不一致')


def diagnoses(stage, verdict):
    rows = stage['tasks'].get('diagnoses', {})
    explicit = {item['id']: item for item in stage['inputs'].get('diagnoses', [])}
    if explicit.keys() - rows.keys():
        raise ValueError('显式定界台账缺失')
    outcomes = []
    automatic = []
    for key, row in rows.items():
        if not isinstance(row, dict):
            raise ValueError('定界台账条目须为对象')
        source, anomaly = origin(row, key, explicit, stage)
        if anomaly is not None:
            automatic.append((row['evaluation'], row['anomaly_index']))
        outcomes.append(diagnosis(row, source, anomaly, stage))
    if 'diagnoses' in stage['tasks'] and stage.get('next_action') != 'cancelled':
        check_coverage(automatic, stage)
    expected = verdict(outcomes)
    conclusions = stage['conclusions']
    # 旧版三组台账可省略定界结论。
    default = None if 'diagnoses' in stage['tasks'] else 'not_evaluated'
    if conclusions.get('demarcation', default) != expected:
        raise ValueError('阶段定界结论与逐题台账不一致')
