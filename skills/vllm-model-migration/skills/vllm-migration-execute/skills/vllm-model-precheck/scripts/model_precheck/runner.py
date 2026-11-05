"""执行预检并保留独立分支事实，最后发布终态。"""
from datetime import datetime, timezone
import socket

from .evidence import begin_attempt, write_json
from .model import inspect_model
from .registry import inspect_optional, inspect_registry, source_inventory
from .review import inspect_reviews
from .routing import choose_route


READ_ERRORS = (OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError)


def collect_facts(request):
    facts = {'model': {}, 'sources': {}, 'reviews': [], 'errors': []}
    try:
        facts['model'] = inspect_model(request)
    except READ_ERRORS as error:
        facts['errors'].append('模型资料不可用：' + str(error))
    for name, source in request['sources'].items():
        try:
            facts['sources'][name] = source_inventory(source)
        except READ_ERRORS as error:
            facts['sources'][name] = {'error': str(error)}
            facts['errors'].append(name + ' 源码不可用：' + str(error))
    facts['registry'] = inspect_registry(request['sources']['vllm'], facts['model'].get('architectures', []))
    facts['optional'] = inspect_optional(request)
    facts['reviews'] = inspect_reviews(request)
    return facts


def run(request, target, run_id):
    attempt, manifest = begin_attempt(target, request, run_id)
    facts = collect_facts(request)
    route = choose_route(request, facts)
    manifest['details'] = {**facts, **route}
    manifest['environment'] = {'hostname': socket.gethostname(), 'mode': 'static_read_only'}
    for name in ('model', 'sources', 'registry', 'reviews', 'optional'):
        path = attempt / (name + '-snapshot.json')
        write_json(path, facts[name])
        manifest['evidence'][name] = str(path)
    manifest['status'] = 'blocked' if route['route'] == 'blocked' else 'completed'
    manifest['conclusions']['version_materials'] = route['version_materials']
    manifest['conclusions']['support'] = 'pass' if route['route'] == 'supported_candidate' else 'review_required'
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(attempt / 'precheck-manifest.json', manifest)
    return (3 if manifest['status'] == 'blocked' else 0), {
        'manifest': str(attempt / 'precheck-manifest.json'), 'status': manifest['status'],
        'route': route['route'], 'conclusions': manifest['conclusions']}
