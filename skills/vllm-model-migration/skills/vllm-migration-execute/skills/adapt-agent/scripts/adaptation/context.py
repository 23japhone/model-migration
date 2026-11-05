"""模型、预检与上游来源身份；预检待适配结论不会被当成运行通过。"""
from pathlib import Path

from .evidence import bound, read_json, stable
from .contract import unique_strings
from .source import inventory
from .redaction import redact

ERRORS = (OSError, ValueError, TypeError, KeyError)


def model_identity(model):
    root = Path(model['root'])
    for name, expected in model['files'].items():
        if stable(root / name)[1] != expected:raise ValueError('模型资产 SHA256 不一致：' + name)
    actual = {str(p.relative_to(root)) for p in root.rglob('*')
              if p.suffix in ('.safetensors', '.bin', '.pt', '.pth')}
    if actual != set(model['weight_files']):raise ValueError('完整权重集合不一致')
    config, digest = read_json(root / model['config'])
    unique_strings(config.get('architectures'))
    if model['architecture'] not in config['architectures']:raise ValueError('原始 architecture 不一致')
    return {'config': config, 'config_sha256': digest, 'architectures': config['architectures']}


def precheck_outer(receipt):
    if (receipt.get('schema_version') != '1.0' or receipt.get('task_id') != 'vllm-model-precheck'
            or receipt.get('status') != 'completed' or not isinstance(receipt.get('run_id'), str)
            or not receipt['run_id'].strip()):
        raise ValueError('预检外层身份或终态无效')
    for key, kind in [('inputs', dict), ('evidence', dict), ('conclusions', dict), ('uncovered', list)]:
        if not isinstance(receipt.get(key), kind):raise ValueError('预检共同契约字段缺失')
    nested_objects(receipt)
    values = receipt['conclusions'].values()
    if any(v not in ('pass', 'fail', 'review_required', 'not_evaluated') for v in values):
        raise ValueError('预检结论值无效')
    if receipt['conclusions'].get('version_materials') != 'pass' or 'fail' in values:
        raise ValueError('预检版本资料或结论不可用')


def nested_objects(receipt):
    groups = [(receipt, ['details']), (receipt['inputs'], ['model', 'target', 'sources'])]
    for parent, keys in groups:
        for key in keys:
            if not isinstance(parent.get(key), dict):raise ValueError('预检嵌套对象无效：' + key)
    details = receipt['details']
    for key in ['model', 'sources']:
        if not isinstance(details.get(key), dict):raise ValueError('预检事实对象无效：' + key)


def precheck_sources(receipt, request, frozen):
    for name, source in request['sources'].items():
        original = receipt['inputs'].get('sources', {}).get(name, {})
        if not isinstance(original, dict):raise ValueError('预检来源输入须为对象')
        if original.get('revision') != source['revision']:raise ValueError('源码 revision 与预检不一致：' + name)
        snapshot = receipt['details']['sources'].get(name, {})
        if not isinstance(snapshot, dict):raise ValueError('预检来源事实须为对象')
        observed = snapshot.get('files', [])
        hashes = {item['path']: item['sha256'] for item in observed}
        baseline = frozen[name]['baseline']['files']
        expected = {path: item['sha256'] for path, item in baseline.items() if path.endswith('.py')}
        if not hashes or hashes != expected:raise ValueError('完整基线 Python 内容与预检不一致：' + name)


def selected_candidate(details, architecture):
    if details.get('route') != 'adaptation_required':raise ValueError('预检路线不要求实现适配，应按原路线交接')
    if details.get('selected_architecture') != architecture:raise ValueError('预检选中 architecture 与请求不一致')
    matrix = details.get('support_matrix')
    if not isinstance(matrix, list) or any(not isinstance(item, dict) for item in matrix):
        raise ValueError('预检 support_matrix 无效')
    matches = [item for item in matrix if item.get('architecture') == architecture]
    if len(matches) != 1 or matches[0].get('route') != 'adaptation_required':
        raise ValueError('预检选中候选矩阵未要求实现适配')


def precheck_identity(request, model, sources):
    receipt = bound(request['precheck'])
    precheck_outer(receipt)
    details = receipt.get('details', {})
    selected_candidate(details, request['model']['architecture'])
    if details.get('model', {}).get('config_sha256') != model['config_sha256']:raise ValueError('预检模型配置身份不匹配')
    if any(receipt['inputs'].get('target', {}).get(k) != v for k, v in request['target'].items()):
        raise ValueError('预检目标版本不一致')
    if receipt['inputs'].get('model', {}).get('modalities') != request['model']['modalities']:
        raise ValueError('预检请求模态不一致')
    precheck_sources(receipt, request, sources)
    return {'path': request['precheck']['path'], 'sha256': request['precheck']['sha256'], 'route': details['route']}


def references(request):
    result = []
    for item in request['references']:
        if stable(Path(item['path']))[1] != item['sha256']:raise ValueError('参考资料 SHA256 不一致')
        result.append(item)
    return redact(result)


def collect(request):
    result = {'sources': {}, 'errors': []}
    for label, operation in [('model', lambda: model_identity(request['model'])),
                             ('references', lambda: references(request))]:
        try:result[label] = operation()
        except ERRORS as error:result['errors'].append(label + '：' + str(error))
    for name, source in request['sources'].items():
        try:
            baseline = inventory(source['baseline'])
            origin = bound(source['origin'])
            if origin.get('revision') != source['revision'] or origin.get('tree_sha256') != baseline['fingerprint']:
                raise ValueError('上游来源 revision 或完整基线身份不匹配')
            if not isinstance(origin.get('acquisition'), str) or not origin['acquisition'].strip():
                raise ValueError('来源缺少获取依据')
            inventory(source['working'])
            result['sources'][name] = {'baseline': baseline, 'origin': origin}
        except ERRORS as error:result['errors'].append(name + '：' + str(error))
    if 'model' in result and len(result['sources']) == len(request['sources']):
        try:result['precheck'] = precheck_identity(request, result['model'], result['sources'])
        except ERRORS as error:result['errors'].append('precheck：' + str(error))
    return redact(result)
