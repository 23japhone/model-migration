"""建立映射与独立复验的组合根；有变更但未通过仍不能完成。"""
import json
from pathlib import Path

from .evidence import begin_attempt, now, write_json
from .inputs import collect_inputs
from .plugin import install_plugin
from .process import run_probe
from .source import apply_source


def matching(request, observed):
    expected = request['implementation']
    return observed is not None and all(observed.get(k) == expected[k] for k in ('module', 'class_name'))


def advance(attempt, manifest, stage):
    manifest['details']['stage'] = stage
    write_json(attempt / 'registration-manifest.json', manifest)


def effect(request, before, attempt):
    observed = before['mapping']
    if observed is not None and not matching(request, observed):
        raise ValueError('已有 architecture 映射冲突，不允许覆盖')
    if request['mode'] == 'source':
        if matching(request, observed):
            return {'action': 'reused', 'registry_sha256': request['target']['registry_sha256']}
        if 'registry' not in request['allowed_changes']:
            raise ValueError('未允许修改 registry，不能建立缺失映射')
        return apply_source(request, attempt)
    if request['plugin']['entrypoint'] not in request['runtime']['plugins']:
        raise ValueError('请求未允许加载目标 plugin，不能宣告注册完成')
    if 'plugin' not in request['allowed_changes']:
        if (before.get('plugin') or {}).get('loaded') and matching(request, observed):
            return {'action': 'reused'}
        raise ValueError('未允许创建或安装 plugin')
    return install_plugin(request, attempt)


def finish(attempt, manifest, status):
    manifest['status'] = status
    manifest['finished_at'] = now()
    if status == 'completed':
        manifest['conclusions']['registration'] = 'pass'
    else:
        manifest['conclusions']['registration'] = 'review_required'
    path = attempt / 'registration-manifest.json'
    write_json(path, manifest)
    output = {'manifest': str(path), 'status': status, 'conclusions': manifest['conclusions']}
    check_request = attempt / 'registration-check.request.json'
    if check_request.is_file():
        output['check_request'] = str(check_request)
    code = {'completed': 0, 'blocked': 3, 'failed': 1}[status]
    return code, output


def run(request, target, run_id, operation):
    attempt, manifest = begin_attempt(target, request, run_id, operation)
    inputs = collect_inputs(request)
    manifest['details'] = {'inputs': inputs, 'stage': 'inputs', 'errors': list(inputs['errors'])}
    write_json(attempt / 'inputs-snapshot.json', inputs)
    manifest['evidence']['inputs'] = str(attempt / 'inputs-snapshot.json')
    if inputs['errors']:
        return finish(attempt, manifest, 'blocked')
    try:
        if operation == 'register':
            advance(attempt, manifest, 'before_probe')
            before = run_probe(request, attempt, 'before')
            manifest['details']['before_check'] = before
            advance(attempt, manifest, 'registration_effect')
            result = effect(request, before, attempt)
            manifest['details'].update(effect=result, action=result['action'])
            request = json.loads(json.dumps(request))
            request['target']['registry_sha256'] = result.get('registry_sha256', request['target']['registry_sha256'])
            write_json(attempt / 'registration-check.request.json', request)
        else:
            manifest['details']['action'] = 'checked'
        advance(attempt, manifest, 'after_probe')
        checked = run_probe(request, attempt, 'after')
        manifest['details']['check'] = checked
    except ValueError as error:
        manifest['details']['errors'].append(str(error))
        return finish(attempt, manifest, 'blocked')
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        manifest['details']['errors'].append(str(error) or '本次检查被取消')
        manifest['diagnostic'] = {'type': type(error).__name__, 'message': str(error) or '本次检查被取消'}
        return finish(attempt, manifest, 'failed')
    return finish(attempt, manifest, 'completed')
