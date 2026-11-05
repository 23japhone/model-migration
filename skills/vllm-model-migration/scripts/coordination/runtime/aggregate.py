"""分别保留阶段状态、目标结论和带来源的资源观察。"""
from .sources import bound


def collect(manifest, key, value, source):
    mapping = {'preparation': ('preparation',), 'migration': ('migration','functional_validation'),
               'dataset_preparation': (), 'precision': ('precision_testing','evaluation_validity','comparability','precision_target')}
    for field in mapping[key]:
        manifest['conclusions'][field] = value['conclusions'].get(field, 'not_evaluated')
    manifest['uncovered'].extend(value['uncovered'])
    manifest['results'].update(value.get('results', {}))
    for resource in value.get('resources', []) if key == 'precision' else []:
        manifest['resources'].append({'source': source, 'observed_at': resource.get('observed_at'),
            'reported_at': value.get('finished_at'),
            'observation': '历史来源观察，未刷新服务' if manifest['operation'] == 'analyze' else '阶段报告观察；不声明持续存活',
            'resource': resource})
    if key == 'precision' and manifest['stages'][key].get('blocked_evaluations'):
        manifest['conclusions']['precision_testing'] = 'not_evaluated'
        for field in ('evaluation_validity','comparability','precision_target'):
            if manifest['conclusions'][field] == 'pass':
                manifest['conclusions'][field] = 'not_evaluated'
    if key == 'migration' and value.get('deployment'):
        manifest['resources'].append({'source': source,
                                      'observed_at': bound(value['tasks']['verify']['receipt']).get('finished_at'),
                                      'reported_at': value.get('finished_at'),
                                      'observation': '部署交接观察；服务生命周期归 verify-agent',
                                      'handoff': value['deployment']})


def conclude(manifest):
    rows = list(manifest['stages'].values())
    status = 'completed' if rows and all(r['status'] == 'completed' for r in rows) else 'blocked'
    if any(r['status'] == 'failed' for r in rows):
        status = 'failed'
    manifest['status'] = status
    manifest['success'] = status == 'completed' and all(r.get('exit_code') == 0 for r in rows)
    manifest['next_action'] = 'complete' if manifest['success'] else 'target_not_met' if status == 'completed' else 'resolve_stage_outcomes'
    if status != 'completed':
        pending = [r.get('next_action') for r in rows if r['status'] != 'completed' and r.get('next_action')]
        if pending:
            manifest['next_action'] = pending[-1]
