"""发现完整本地资产并标记身份待复核；只有确认不存在才切换仓库。"""
import os
from pathlib import Path

from .assets import inspect_assets
from .providers import HuggingFaceSource, ModelScopeSource
from .request import normalize_request
from .sources import normalize_description


def cache_roots():
    home = Path.home()
    hf_home = Path(os.environ.get('HF_HOME', Path(os.environ.get('XDG_CACHE_HOME', home / '.cache')) / 'huggingface'))
    hf = Path(os.environ.get('HF_HUB_CACHE', os.environ.get('HUGGINGFACE_HUB_CACHE', hf_home / 'hub')))
    ms = Path(os.environ.get('MODELSCOPE_CACHE', home / '.cache/modelscope/hub'))
    return {'huggingface': hf.expanduser(), 'modelscope': ms.expanduser()}


def cached_candidates(repository, roots=None):
    roots = roots or cache_roots()
    repo_pattern = 'models--' + repository.replace('/', '--') if isinstance(repository, str) else 'models--*'
    candidates = list(roots['huggingface'].glob(repo_pattern + '/snapshots/*'))
    pattern = repository if isinstance(repository, str) else '*/*'
    ms = roots['modelscope']
    prefixes = {ms, ms / 'models', ms / 'customer-models'}
    if ms.name == 'hub':
        prefixes.add(ms.parent / 'models')
    for prefix in prefixes:
        candidates.extend(prefix.glob(pattern))
        candidates.extend(prefix.glob(pattern.replace('.', '___')))
        snapshot = repository.replace('/', '--') if isinstance(repository, str) else '*--*'
        candidates.extend(prefix.glob(snapshot + '/snapshots/*'))
    candidates.extend((roots['huggingface'] / 'customer-models').glob(pattern))
    return sorted({p.absolute() for p in candidates if p.is_dir() and (p / 'config.json').is_file()})


def local_request(path):
    return normalize_request({'source': 'local', 'path': str(path), 'scope': 'full',
                              'task': 'chat', 'modalities': ['text']})


def lookup(provider, repository):
    request = normalize_request({'source': provider, 'repository': repository,
                                 'path': '/unused-model-resolution', 'scope': 'full', 'task': 'chat'})
    factory = ModelScopeSource if provider == 'modelscope' else HuggingFaceSource
    try:
        value = factory(request).describe()
    except Exception as error:
        # 鉴权和网络失败不能伪装成仓库不存在；未知 SDK 错误保持失败。
        response = getattr(error, 'response', None)
        if getattr(response, 'status_code', None) == 404 and 'RevisionNotFound' not in type(error).__name__:
            return None
        raise
    value = normalize_description(value, request)
    return {'revision': value['resolved_revision'], 'source_snapshot': value}


def usable_local(paths, inspect):
    valid, invalid = [], []
    for path in paths:
        if not Path(path).is_dir():
            continue
        request = local_request(path)
        try:
            asset = inspect(request)
            valid.append((request, asset))
        except (OSError, ValueError) as error:
            invalid.append({'path': str(path), 'reason': type(error).__name__})
    return valid, invalid


def remote_request(model, remote_lookup):
    repository = model['model_repository_id']
    if not isinstance(repository, str):
        raise ValueError('本地无可用权重，必须补充 model.model_repository_id')
    for provider in ('modelscope', 'huggingface'):
        found = remote_lookup(provider, repository)
        if found is None:
            continue
        destination = model['path']
        if type(destination) is int and destination == -1:
            # 下载到对应官方缓存根中的独立 local_dir；不冒充 SDK 管理的快照。
            destination = str(cache_roots()[provider] / 'customer-models' / repository)
        value = {'source': provider, 'path': destination, 'repository': repository,
                 'revision': found['revision'], 'scope': 'full', 'task': 'chat', 'modalities': ['text']}
        snapshot = found.get('source_snapshot', {})
        if snapshot.get('snapshot_identity'):
            value['expected_snapshot'] = snapshot['snapshot_identity']
        return {'request': normalize_request(value), 'resolution': 'download_or_verify_existing',
                'source_snapshot': found.get('source_snapshot')}
    raise ValueError('ModelScope 与 Hugging Face 均未找到模型仓库，请更正 model.model_repository_id')


def resolve(model, candidates=None, inspect=inspect_assets, remote_lookup=lookup):
    explicit = isinstance(model['path'], str)
    paths = [Path(model['path'])] if explicit else (
        cached_candidates(model['model_repository_id']) if candidates is None else candidates)
    valid, invalid = usable_local(paths, inspect)
    if len(valid) > 1:
        raise ValueError('缓存中存在多个可用模型快照，请明确 model.path')
    if valid:
        request, asset = valid[0]
        if 'identity' in asset:
            request['expected_identity'] = asset['identity']
        review = isinstance(model['model_repository_id'], str)
        return {'request': request, 'resolution': 'local_identity_review_required' if review else 'local_weights_verified',
                'asset': asset}
    result = remote_request(model, remote_lookup)
    result['invalid_local_candidates'] = invalid
    return result
