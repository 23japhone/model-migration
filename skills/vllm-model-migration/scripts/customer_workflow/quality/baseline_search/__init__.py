"""没有客户 GPU 基线时，搜索已发布的同模型 EvalScope 原生评测。"""
import hashlib
import re
from pathlib import Path

from coordination.runtime import sources
from ...state import identity
from ..resolution import publish
from .archive import candidate
from .publisher import REPO, published_files
from .transport import ERRORS, MAX_BYTES, Search


def qualify(task, origin, gpu_evidence):
    from .qualification import qualify as inspect
    return inspect(task, origin, gpu_evidence)


def matches_weights(records, model):
    weights = model.get('weight_files', [])
    actual = model.get('files', {})
    stated = {row['path']: row.get('sha256') for row in records}
    return bool(weights) and all(actual.get(name) and actual[name] == stated.get(name) for name in weights)


def model_snapshot(resolution, deployed):
    asset = resolution.get('asset', {})
    snapshots = [resolution.get('source_snapshot'), asset.get('provenance')]
    for snapshot in filter(None, snapshots):
        files = snapshot.get('source_files', [])
        repo = snapshot.get('repository', '')
        if snapshot.get('provider') not in ('modelscope', 'huggingface') or not re.fullmatch(REPO, repo):
            continue
        if identity(files) == snapshot.get('snapshot_identity') and matches_weights(files, deployed):
            yield {'repository':repo,'provider':snapshot['provider'],'snapshot_identity':identity(files)}


def reviewed_model(reference, resolution, deployed):
    from ...model_identity import checked_review, local_binding
    if not reference or not matches_weights(resolution.get('asset', {}).get('files', []), deployed):
        return None
    record = sources.bound(reference)
    repo = record['repository']
    if not isinstance(repo, str) or not re.fullmatch(REPO, repo):
        return None
    binding = local_binding({'model_repository_id':repo}, resolution)
    if checked_review(reference, binding)['conclusion'] == 'match':
        return {'repository':repo,'review':reference}
    return None


def trusted_model(task, observations):
    model = task['model']
    resolution = observations.get('model_resolution')
    if isinstance(model.get('model_repository_id'), str) or not resolution or not observations.get('deployment_receipt'):
        return None
    from .qualification import deployed_model
    deployed = deployed_model(observations['deployment_receipt'])
    candidates = list(model_snapshot(resolution, deployed))
    review = reviewed_model(observations.get('model_identity_review'), resolution, deployed)
    if review:
        candidates.append(review)
    if len({x['repository'] for x in candidates}) > 1:
        raise ValueError('绑定当前权重的原始来源给出了不同的发布仓库')
    if candidates:
        return {**candidates[0], 'deployment_receipt':observations['deployment_receipt']}
    return None


def collect(task, extra, search):
    found = []
    try:
        for index, (origin, public, file) in enumerate(published_files(search, task['model'], extra)):
            if index >= 24:
                search.missing.append('已达到本次评测附件检索上限')
                break
            try:
                uri = public.url(file)
                data = search.json(uri)
                if isinstance(data, dict):
                    ref = candidate(search, public, file, data)
                    if ref:
                        found.append({'gpu_evidence': ref, 'origin': {**origin, 'artifact_url': uri}})
            except ERRORS as error:
                search.missing.append(str(error))
    except ERRORS as error:
        search.missing.append(str(error))
    if not found:
        search.missing.append('缺少绑定同一 GPU 运行的模型权重、原生任务配置、数据范围、评分报告及静态服务条件')
    return {'archives': found, 'sources': list(search.sources.values()), 'missing': list(dict.fromkeys(search.missing))}


def snapshot(task, extra, root):
    key = identity([task['model'].get('model_name'), task['model'].get('model_repository_id'), extra])
    if not isinstance(task['model'].get('model_repository_id'), str):
        key = identity([key, 'same-name-repositories-v2'])
    folder = root / key
    marker = folder / 'published-search.json'
    if marker.exists():
        record = sources.bound(sources.read(marker))
        for row in record['sources']:
            if 'payload' in row:
                ref = row['payload']
                if hashlib.sha256(sources.stable_bytes(ref['path'], MAX_BYTES)).hexdigest() != ref['sha256']:
                    raise ValueError('已固定的公开来源 SHA256 改变：' + row['url'])
        return record
    result = collect(task, extra, Search(folder))
    ref = publish(folder / 'results' / (identity(result) + '.json'), result)
    publish(marker, ref)
    return result


def qualified_sources(task, observations, archives, missing):
    found = {}
    for item in archives:
        try:
            origin = {**item['origin'], 'deployment_receipt': observations.get('deployment_receipt')}
            result = qualify(task, origin, item['gpu_evidence'])
            missing.extend(result['missing'])
            if result.get('baseline'):
                hit = {k: v for k, v in result.items() if k != 'missing'}
                hit['gpu_evidence'] = item['gpu_evidence']
                hit['origin'] = item['origin']
                found.setdefault((origin['repository_id'], identity([hit['protocol'], hit['baseline']])), hit)
        except ERRORS as error:
            missing.append(str(error))
    return found


def discover(task, observations, root):
    root = Path(root)
    extra = observations.get('quality_baseline_sources', [])
    search, missing, found = {}, [], {}
    model_source = None
    try:
        if not isinstance(extra, list) or any(not isinstance(url, str) for url in extra):
            raise ValueError('附加公开基线来源须为 URL 列表')
        sources.protect(root, sources.paths(task.get('model', {}), Path.cwd()))
        model_source = trusted_model(task, observations)
        if model_source:
            task = {**task, 'model':{**task['model'], 'model_repository_id':model_source['repository']}}
        search = snapshot(task, extra, root)
        missing.extend(search['missing'])
        found = qualified_sources(task, observations, search['archives'], missing)
    except ERRORS as error:
        missing.append('公开基线搜索或固定回执无法核验：' + str(error))
    publishers = sorted({repo for repo, _ in found})
    if len(publishers) > 1:
        missing.append('多个同名发布仓库均提供了可核验的基线，不能唯一归属：' + '、'.join(publishers))
        found.clear()
    result = {'status': 'found' if found else 'not_evaluated', 'baselines': list(found.values()),
              'sources': search.get('sources', []), 'missing': list(dict.fromkeys(missing))}
    if model_source:
        result['model_source'] = model_source
    if search:
        try:
            result['receipt'] = publish(root / 'decisions' / (identity(result) + '.json'), result)
        except ERRORS as error:
            result.update(status='not_evaluated', baselines=[], missing=[*result['missing'], str(error)])
    return result
