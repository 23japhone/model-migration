"""优先选择已核验的完整 NPU 测量，保留有效历史分项以便回退。"""
from time import monotonic

from alignment.evidence import EvidenceError, bound, safe

INVALID = (ValueError, TypeError, KeyError, OSError, RuntimeError)


def missing_scores(projection, native):
    evidence = bound(projection)
    present = {(row['metric'], row['subset']) for row in evidence['scores']
               if row.get('value') is not None and not row.get('error')}
    return [{'metric': row['metric'], 'subset': row['subset']} for row in native['scores']
            if (row['metric'], row['subset']) not in present]


def history(native, candidates, invoke, checks):
    best = None
    for index, candidate in enumerate(candidates):
        started = monotonic()
        check = {'receipt': candidate, 'accepted': False}
        checks.append(check)
        try:
            bound(candidate)
            request = {'schema_version': '1.0', 'source': candidate, 'expected': native}
            verified, projection = invoke('analyze', request, 'candidate-' + str(index))
            missing = missing_scores(projection, native)
            if missing:
                check.update(missing_scores=missing, reason='候选缺少部分请求指标')
            if len(missing) == len(native['scores']):
                continue
            result = {'source': 'reused', 'receipt': candidate, 'verification': verified,
                      'measurement': projection, 'candidate_checks': checks, 'measurement_seconds': 0}
            check['verification'] = verified
            if not best or len(missing) < best[0]:
                best = len(missing), check, result
            if not missing:
                break
        except EvidenceError:
            raise
        except INVALID as error:
            check['reason'] = safe(error)
        finally:
            check['seconds'] = monotonic() - started
    return best


def reuse(best, reason=None):
    _, check, result = best
    check['accepted'] = True
    if reason:
        result['measurement_fallback_reason'] = reason
    return result


def select(native, candidates, invoke, *, require_reuse=False, before_fallback=None):
    checks = []
    best = history(native, candidates, invoke, checks)
    if best and (best[0] == 0 or require_reuse):
        return reuse(best)
    if require_reuse:
        reasons = [row['reason'] for row in checks]
        raise ValueError('仅复用验收没有有效候选：' + '；'.join(reasons))
    if checks and before_fallback:
        before_fallback()
    started = monotonic()
    try:
        receipt, projection = invoke('quality-run', native, 'measurement')
        if best and len(missing_scores(projection, native)) > best[0]:
            return reuse(best, '新测量覆盖的有效指标少于已核验历史测量')
    except EvidenceError:
        raise
    except INVALID as error:
        if best:
            return reuse(best, safe(error))
        raise
    return {'source': 'rerun', 'receipt': receipt, 'verification': receipt, 'measurement': projection,
            'candidate_checks': checks, 'measurement_seconds': monotonic() - started}
