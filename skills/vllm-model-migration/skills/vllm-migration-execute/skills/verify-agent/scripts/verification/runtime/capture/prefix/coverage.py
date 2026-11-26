"""按位置统计各适用层的全部分量均有来源的 Token 数。"""
from collections import Counter


def coverage(report, layers, components, observe_values):
    count = report['hit_tokens']
    if count is None:
        return {'source_status': 'unverified', 'verified_hit_tokens': 0}
    if not count:
        return {'source_status': 'miss', 'verified_hit_tokens': 0}
    if not observe_values:
        return {'source_status': 'metadata_only', 'verified_hit_tokens': 0}
    expected = sum(len(components.get(layer['kind'], ())) for layer in layers)
    events = Counter()
    for part in report['components']:
        start, end = part['range']
        events[start] += 1
        events[end] -= 1
    active = last = verified = 0
    for pos, delta in sorted(events.items()):
        if expected and active == expected:
            verified += pos - last
        active += delta
        last = pos
    return {'verified_hit_tokens': verified, 'source_status': 'verified' if verified == count and not report['errors'] else 'incomplete'}
