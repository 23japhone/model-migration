"""以冻结计划为分母生成双端逐调用证据和数值结论。"""
import pickle

from ..evidence import bound
from . import artifacts, native, oracle
from .contract import policy, validate_plan
from .metrics import metrics


READ_ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError, IndexError, EOFError, pickle.UnpicklingError)


def open_sides(req, plan):
    expected = {(c['id'], s['id']) for c in plan.value['calls'] for s in c['slots']}
    sides = {}
    for side in ('gpu', 'npu'):
        if side not in req:
            continue
        bundle = artifacts.package(req[side], side, plan)
        rows, issues = artifacts.index(bundle, expected)
        try:
            native.verify(bundle, plan.value, rows, issues)
        except READ_ERRORS as error:
            issues.append('原生来源或实物清单核验失败：' + str(error))
            rows = {}
        sides[side] = (bundle, rows, {'expected_slots': len(expected), 'verified_slots': 0,
                                     'calls_with_io': 0, 'missing': [], 'issues': issues})
    return sides


def missing(row, call, slot, reason):
    row['missing'].append({'call': call, 'slot': slot, 'reason': str(reason)})


def record_coverage(coverage, key, issue):
    if issue:
        missing(coverage, *key, issue)
    else:
        coverage['verified_slots'] += 1


def capture_call(capture, call, torch):
    bundle, rows, coverage = capture
    values, sources, ordinals = {}, {}, set()
    for slot in call['slots']:
        key = (call['id'], slot['id'])
        item = rows.get(key)
        if item is None:
            missing(coverage, *key, '缺少唯一张量文件')
            continue
        try:
            if torch is None:
                raise ValueError('本地缺少用于校验原生张量的 PyTorch')
            values[slot['id']] = artifacts.verified(bundle, item, slot, torch)
            fields = ('fp8_scaled', 'fp8_scale', 'path', 'sha256', 'shape', 'dtype', 'layout', 'original_shape', 'capture_slice', 'argument', 'native_name')
            sources[slot['id']] = {k: item[k] for k in fields if k in item}
            if type(item.get('ordinal')) is int and item['ordinal'] >= 0:
                ordinals.add(item['ordinal'])
            issue = values[slot['id']].scale_issue
            record_coverage(coverage, key, issue)
        except READ_ERRORS as error:
            missing(coverage, *key, error)
    roles = {slot['role'] for slot in call['slots'] if slot['id'] in values}
    if 'input' in roles and 'output' in roles:
        coverage['calls_with_io'] += 1
    if len(ordinals) > 1:
        coverage['issues'].append(call['id'] + ' 的张量归属多个执行序号')
        values.clear()
    return values, sources, next(iter(ordinals)) if len(ordinals) == 1 else None


def compare_call(call, sides, limits, torch, plan):
    row = {'id': call['id'], 'kind': call['kind'], 'occurrence': call['occurrence'],
           'execution_order': {}, 'sources': {}, 'slots': []}
    values = {}
    for name, capture in sides.items():
        loaded, sources, ordinal = capture_call(capture, call, torch)
        row['execution_order'][name] = ordinal
        row['sources'][name] = sources
        values[name] = loaded
    if len(sides) != 2:
        return row
    for slot in call['slots']:
        key = slot['id']
        result = {'result': 'not_evaluated', 'reason': '缺少可比张量'}
        if key in values['gpu'] and key in values['npu']:
            try:
                result = metrics(values['npu'][key], values['gpu'][key], limits, torch,
                                 device_pair=True, allow_inf=slot.get('allow_matching_inf', False))
            except READ_ERRORS as error:
                result = {'result': 'not_evaluated', 'reason': str(error)}
        row['slots'].append({'slot': key, 'role': slot['role'], **result})
    row['cpu_check'] = oracle.compare(call, values, limits, torch, plan)
    row['roles'] = role_results(row['slots'])
    row['result'] = group_result(row['slots'])
    return row


def group_result(slots):
    outcomes = {slot['result'] for slot in slots}
    if 'fail' in outcomes:
        return 'fail'
    return 'pass' if outcomes == {'pass'} else 'not_evaluated'


def role_results(slots):
    result = {}
    for role in dict.fromkeys(s['role'] for s in slots):
        group = [slot for slot in slots if slot['role'] == role]
        result[role] = {'result': group_result(group), 'planned': len(group),
                        'compared': sum(s['result'] in ('pass', 'fail') or 'elements' in s for s in group),
                        'failed_slots': [s['slot'] for s in group if s['result'] == 'fail']}
    return result


def first_failed(failures, calls):
    if not failures:
        return None
    if len({item['call'] for item in failures}) == 1:
        return failures[0]
    orders = {call['id']: call['execution_order'] for call in calls}
    sequences = []
    for side in ('gpu', 'npu'):
        if any(orders[row['call']][side] is None for row in failures):
            return None
        sequences.append(min(failures, key=lambda item: orders[item['call']][side]))
    return sequences[0] if sequences[0]['call'] == sequences[1]['call'] else None


def capture_context(bundle):
    fields = ('evidence_kind', 'trace_id', 'forward_id', 'phase', 'rank', 'native_index')
    return {key: bundle.value[key] for key in fields if key in bundle.value}


def devices_result(failures, comparable, slots, incomplete):
    if failures:
        return 'mismatch'
    return 'insufficient_evidence' if incomplete or len(comparable) < len(slots) else 'aligned'


def cpu_summary(calls):
    checks = [c['cpu_check'] for c in calls]
    states = {c['result'] for c in checks}
    result = 'mismatch' if 'mismatch' in states else ('aligned' if states == {'aligned'} else 'not_evaluated')
    shared = all(c.get('basis') == 'common' and not c['unassessed_outputs'] for c in checks)
    return {'result': result, 'verified_calls': sum(c['result'] in ('aligned', 'mismatch') for c in checks),
            'common_reference': result if shared else 'not_evaluated'}


def summarize(calls, incomplete):
    slots = [(call['id'], slot) for call in calls for slot in call['slots']]
    failures = [{'call': name, **row} for name, row in slots if row['result'] == 'fail']
    measurements = [{'call': name, **row} for name, row in slots if 'max_abs_error' in row]
    finite = [m for m in measurements if m['max_abs_error'] is not None]
    maximum = max(finite, key=lambda item: item['max_abs_error'], default=None)
    result = devices_result(failures, measurements, slots, incomplete)
    cpu = cpu_summary(calls)
    return {'result': result, 'first_exceedance': first_failed(failures, calls), 'largest_error': maximum,
            'first_exceedance_scope': 'verified_slots_with_observed_call_order',
            'cpu_check': cpu, 'target_status': {'mismatch':'fail', 'aligned':'pass'}.get(result, 'not_evaluated')}


def run(req, request_ref):
    plan = bound(req['plan'])
    validate_plan(plan.value)
    limits = policy(req.get('tensor_alignment', {}))
    sides = open_sides(req, plan)
    try:
        import torch
    except ImportError:
        torch = None
    calls = [compare_call(call, sides, limits, torch, plan.value) for call in plan.value['calls']]
    coverage = {side: capture[2] for side, capture in sides.items()}
    incomplete = any(row['missing'] or row['issues'] for row in coverage.values())
    status = 'incomplete' if incomplete else 'complete'
    uncovered = plan.value.get('scope_uncovered', [])
    missing_devices = sorted({'gpu', 'npu'} - sides.keys())
    summary = ({'result': 'insufficient_evidence', 'target_status': 'not_evaluated'} if missing_devices
               else summarize(calls, incomplete or uncovered))
    kinds = {side: bundle.value.get('evidence_kind', 'unspecified') for side, (bundle, _, _) in sides.items()}
    return {'task_id': 'vllm-precision-compare', 'status': 'completed', 'operation': 'tensor', 'format': 'tensor-comparison-v1',
            'case': plan.value['case'], 'position': plan.value['position'], 'plan': plan.ref, 'request': request_ref,
            'capture_status': status, 'coverage': coverage, 'missing_devices': missing_devices, 'scope_uncovered': uncovered,
            'calls': calls, 'tensor_alignment': limits,
            'evidence': {'request': request_ref, 'plan': plan.ref,
                         **{side: bundle.ref for side, (bundle, _, _) in sides.items()}},
            'side_context': {side: capture_context(bundle) for side, (bundle, _, _) in sides.items()},
            'evidence_kind': kinds, **summary}
