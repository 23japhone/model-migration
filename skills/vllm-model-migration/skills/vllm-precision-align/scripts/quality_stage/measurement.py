"""按已核验的测量口径选择一次原生评测，保留每个历史候选的核验理由。"""
from time import monotonic

from alignment.evidence import EvidenceError, bound, safe


def coverage(projection, native):
    evidence = bound(projection)
    static = evidence.get('static_conditions') or {}
    missing = [key for key in ('chat_template_sha256', 'tokenizer_sha256', 'server_generation')
               if not static.get(key)]
    if missing:
        raise ValueError('候选的静态测量条件缺失：' + '、'.join(missing))
    present = {(s['metric'], s['subset']) for s in evidence['scores'] if s.get('value') is not None}
    wanted = {(s['metric'], s['subset']) for s in native['scores']}
    if wanted - present:
        raise ValueError('候选缺少本次要求的原生总分或分项')


def select(native, candidates, invoke, *, require_reuse=False, before_fallback=None):
    checks = []
    for index, candidate in enumerate(candidates):
        started = monotonic()
        try:
            bound(candidate)
            request = {'schema_version': '1.0', 'source': candidate, 'expected': native}
            verified, projection = invoke('analyze', request, 'candidate-' + str(index))
            coverage(projection, native)
        except EvidenceError:
            raise
        except (ValueError, TypeError, KeyError, OSError) as error:
            checks.append({'receipt': candidate, 'accepted': False, 'reason': safe(error),
                           'seconds': monotonic() - started})
            continue
        checks.append({'receipt': candidate, 'accepted': True, 'verification': verified,
                       'seconds': monotonic() - started})
        return {'source': 'reused', 'receipt': candidate, 'verification': verified,
                'measurement': projection, 'candidate_checks': checks, 'measurement_seconds': 0}
    if require_reuse:
        reasons = [row['reason'] for row in checks]
        raise ValueError('仅复用验收没有有效候选：' + '；'.join(reasons))
    if checks and before_fallback:
        before_fallback()
    started = monotonic()
    receipt, projection = invoke('quality-run', native, 'measurement')
    return {'source': 'rerun', 'receipt': receipt, 'verification': receipt, 'measurement': projection,
            'candidate_checks': checks, 'measurement_seconds': monotonic() - started}
