"""只从当前模型来源和实际 config 生成默认服务名，不猜测仓库身份。"""
import re

from coordination.runtime import sources
from model_cache import checked
from .. import model_identity
from .policy import REPOSITORY, container_name, unspecified
from .state import bind


def config_evidence(asset):
    path = checked(asset['path'], 'config.json')
    source = sources.ref(path)
    record = next((row for row in asset['files'] if row['path'] == 'config.json'), {})
    config = sources.bound(source)
    if record.get('sha256') != source['sha256'] or config != asset['metadata']['config']:
        raise ValueError('模型 config 元数据或内容与实际文件不一致')
    return config, source


def config_name(config):
    candidate = config.get('_name_or_path')
    if isinstance(candidate, str) and re.fullmatch(REPOSITORY, candidate):
        return candidate, 'config_id'
    candidate = config.get('model_type')
    if isinstance(candidate, str) and candidate.strip():
        return candidate, 'model_type'
    architectures = config.get('architectures')
    if (isinstance(architectures, list) and len(architectures) == 1
            and isinstance(architectures[0], str) and architectures[0].strip()):
        return architectures[0], 'architecture'
    raise ValueError('无法确定默认服务名，请明确 model.model_name')


def local_name(asset, review):
    config, source = config_evidence(asset)
    repositories = {value for value in (review.get('repository'), asset.get('provenance', {}).get('repository'))
                    if isinstance(value, str) and re.fullmatch(REPOSITORY, value)}
    if len(repositories) > 1:
        raise ValueError('模型来源中的仓库 ID 冲突，不能用架构名掩盖冲突')
    name, origin = (next(iter(repositories)), 'repository') if repositories else config_name(config)
    identity = {'path': asset['path'], 'asset_identity': asset['identity']}
    evidence = [source, *([review['review']] if 'review' in review else [])]
    return name, origin, identity, evidence


def selection(session, task, observations):
    resolution = observations['model_resolution']
    review = model_identity.verify(task, observations)
    proof = session.artifact('default-model-source', resolution)
    if resolution['request']['source'] == 'local':
        name, origin, identity, evidence = local_name(resolution['asset'], review)
        return name, origin, identity, [proof, *evidence]
    request = resolution['request']
    repository = request['repository']
    if not isinstance(repository, str) or not re.fullmatch(REPOSITORY, repository):
        raise ValueError('模型来源未提供有效仓库 ID，请明确 model.model_name')
    identity = {key: request[key] for key in ('source', 'repository', 'revision', 'path')}
    identity['snapshot'] = request.get('expected_snapshot')
    return repository, 'repository', identity, [proof]


def resolve(session, task, observations):
    if not unspecified(task['model']['model_name']):
        return True
    name, origin, identity, evidence = selection(session, task, observations)
    from ..contract import text
    text(name, 'model.model_name')
    values = {'model.model_name': name}
    if unspecified(task['environment']['container_name']):
        values['environment.container_name'] = container_name(name)
    bind(session, task, 'model', {'values': values, 'origin': origin, 'identity': identity, 'evidence': evidence})
    return True
