"""绑定完整总入口、原始部署和有效基线；不重新执行任务。"""
from pathlib import Path

from coordination.request import validate as entry_request
from coordination.runtime import continuation, receipts, sources
from .contracts import require
from .deployment import configuration
from device_contract import effective_runtime

STAGES = {'preparation': ('preparation', 'vllm-migration-prepare'),
          'dataset_preparation': ('preparation', 'vllm-migration-prepare'),
          'migration': ('migration', 'vllm-migration-execute'),
          'precision': ('precision', 'vllm-precision-align')}


def entry(source):
    value = sources.bound(source)
    receipts.common(value, 'vllm-model-migration')
    require(value.get('operation') == 'run' and value['status'] == 'completed',
            '交接需要已完成的真实运行，不能使用历史分析或未完成流程')
    entry_request(value['inputs'], 'run')
    require(value['inputs']['scope'] == 'deployment_precision', '仅完整迁移流程生成调优交接')
    path = Path(source['path'])
    require(value['evidence'].get('manifest') == str(path), '总入口 manifest 位置不一致')
    require(value['evidence'].get('ledger') == str(path.parent/'task-ledger.json'), '总入口台账位置不一致')
    require(value['evidence'].get('handoff') == str(path.parent/'entry-handoff.json'), '总入口交接位置不一致')
    require(sources.bound(value['request_source']) == value['inputs'], '总入口原请求已变化')
    require(sources.read(value['evidence']['ledger']) == value['stages'], '总入口台账与 manifest 不一致')
    handoff = sources.bound(value['handoff'])
    require(value['handoff']['path'] == value['evidence']['handoff'], '总入口 handoff 引用不一致')
    require(handoff['ledger'] == sources.ref(Path(value['evidence']['ledger'])), '总入口台账摘要不一致')
    for key in ('task_id', 'run_id', 'status', 'stages', 'conclusions', 'results', 'resources', 'lifecycle'):
        require(handoff.get(key) == value.get(key), '总入口 handoff 投影不一致：'+key)
    require(set(value['stages']) == set(value['inputs']['stages']), '总入口阶段集合不一致')
    stages = {key: stage(value, key) for key in value['stages']}
    require({'migration', 'precision'} <= stages.keys(), '完整流程缺少迁移或精度阶段')
    continuation.reusable(stages['migration'])
    return value, stages


def stage(value, key):
    row = value['stages'][key]
    kind, skill = STAGES[key]
    require(row.get('status') == 'completed' and not row.get('blocked_evaluations'), '阶段尚未完整完成：'+key)
    require(row.get('source') == value['inputs']['stages'][key], '阶段来源与顶层请求不一致')
    raw = sources.bound(row['receipt'])
    receipts.validate(raw, Path(row['receipt']['path']), kind, skill)
    require(raw['status'] == 'completed' and row['conclusions'] == raw['conclusions'], '阶段终态不一致')
    require(row['exit_code'] == receipts.expected_code(raw, kind), '阶段退出码不一致')
    snapshot = sources.bound(row['input_snapshot'])
    if row.get('mode') == 'reuse' and key == 'migration':
        require(row['receipt'] == row['source']['result'] and snapshot == raw, '复用迁移身份不一致')
    else:
        expected = receipts.preparation_inputs(snapshot) if kind == 'preparation' else snapshot
        require(expected == raw['inputs'], '冻结阶段输入与回执不一致')
    if key == 'precision':
        require(raw.get('operation') == 'run' and value['results'] == raw['results'], '基线结果不是本次运行投影')
    return raw


def deployment(migration):
    verification = sources.bound(migration['tasks']['verify']['receipt'])
    require(verification.get('operation') == 'verify', '缺少原始部署验证输入')
    handoff = sources.bound(migration['deployment'])
    request = verification['inputs']
    for key in ('model', 'runtime'):
        require(handoff.get(key) == request.get(key), '部署交接与验证输入不一致：'+key)
    require(handoff.get('endpoint') == request['service']['endpoint'], '部署端点与验证输入不一致')
    effective_runtime(handoff, request)
    return request, handoff


def deployment_configuration(reference):
    """只读取得独立 verify/review 的有效声明，不要求它属于同一运行实例。"""
    report = sources.bound(reference)
    receipts.common(report, 'verify-agent')
    require(report['status'] == 'completed' and report.get('success') is True
            and all(report['conclusions'].get(key) == 'pass' for key in receipts.VERIFY_KEYS),
            '基线部署回执尚未全部验证通过')
    handoff = sources.bound(continuation.deployment_reference(report))
    raw = sources.bound(report['inputs']['source']) if report.get('operation') == 'review' else report
    receipts.common(raw, 'verify-agent')
    require(raw.get('operation') == 'verify' and raw['status'] == 'completed'
            and all(raw['conclusions'].get(key) == 'pass' for key in receipts.VERIFY_KEYS - {'semantic'}),
            '基线缺少已完成的原始部署验证')
    receipts.original_evidence(report)
    if raw is not report:
        receipts.original_evidence(raw)
    request = raw['inputs']
    require(all(handoff.get(key) == request.get(key) for key in ('model', 'runtime'))
            and handoff.get('endpoint') == request['service']['endpoint'],
            '基线部署交接与原始声明不一致')
    require(handoff.get('final_service', {}).get('state') == 'retained', '基线部署未交接保留的服务')
    return configuration({**request, 'runtime': effective_runtime(handoff, request)})


def match_deployment(actual, expected):
    if actual == expected:
        return  # 原回执身份路径已由完整流程核验，保持既有行为。
    require(deployment_configuration(actual) == deployment_configuration(expected),
            '基线模型或声明的部署配置与本次迁移不一致')


def baselines(value, stages):
    migration = stages['migration']
    expected = migration.get('review_revalidated', migration['tasks']['verify']['receipt'])
    bindings = value['inputs']['stages']['precision'].get('bindings', {})
    selected = [row['id'] for row in stages['precision']['inputs']['evaluations']
                if bindings.get(row['id'], {}).get('deployment') == 'migration']
    require(bool(selected), '缺少消费本次迁移的精度基线')
    rows = stages['precision']['tasks']['evaluations']
    results = []
    for key in selected:
        raw = sources.bound(rows[key]['receipt'])
        match_deployment(raw['inputs'].get('deployment'), expected)
        require(raw['conclusions'].get('evaluation_validity') == 'pass', '基线测量无效或待复核')
        require(raw.get('operation') == 'run', '历史分析不能替代实际基线测量')
        results.append(raw)
    return results


def load(source):
    value, stages = entry(source)
    request, handoff = deployment(stages['migration'])
    return value, request, handoff, baselines(value, stages)
