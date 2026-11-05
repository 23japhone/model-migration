"""绑定 Agent 的模型来源复核；本地资产完整不等于仓库身份一致。"""
from pathlib import Path
import re

from coordination.request import fields, reference
from coordination.runtime import sources
from .recovery import DecisionRequired


def local_binding(model, resolution):
    request, asset = resolution['request'], resolution['asset']
    identity = asset['identity']
    fields(identity, ('format', 'sha256'))
    if (identity['format'] != 'vllm-model-file-list-v1'
            or not isinstance(identity['sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', identity['sha256'])):
        raise ValueError('本地模型缺少有效内容身份')
    path = str(Path(request['path']).resolve())
    if request.get('expected_identity') != identity or str(Path(asset['path']).resolve()) != path:
        raise ValueError('模型准备请求未绑定本地资产身份')
    return {'repository': model['model_repository_id'], 'path': path, 'identity': identity}


def checked_review(source, binding):
    reference(source)
    value = sources.bound(source)
    fields(value, ('repository', 'path', 'identity', 'conclusion', 'reason', 'evidence'))
    if any(value[key] != expected for key, expected in binding.items()):
        raise ValueError('模型身份复核不属于当前仓库、本地路径或内容摘要')
    if value['conclusion'] not in ('match', 'conflict', 'unknown'):
        raise ValueError('模型身份复核结论必须是 match/conflict/unknown')
    if not isinstance(value['reason'], str) or not value['reason'].strip():
        raise ValueError('模型身份复核必须说明依据')
    if not isinstance(value['evidence'], list) or not value['evidence']:
        raise ValueError('模型身份复核缺少实际来源证据')
    for evidence in value['evidence']:
        reference(evidence)
        if sources.ref(evidence['path']) != evidence:
            raise ValueError('模型身份复核证据内容变化')
    return value


def verify(task, observations):
    model = task['model']
    resolution = observations['model_resolution']
    request = resolution['request']
    if isinstance(model['path'], str) and Path(request['path']).resolve() != Path(model['path']).resolve():
        raise ValueError('模型解析结果改变了客户显式路径')
    if request['source'] != 'local':
        if (request['source'] not in ('modelscope', 'huggingface')
                or request.get('repository') != model['model_repository_id']):
            raise ValueError('模型解析结果改变了客户仓库身份')
        return {'conclusion': 'remote_source', 'repository': request['repository']}
    binding = local_binding(model, resolution)
    if model['model_repository_id'] == -1:
        return {**binding, 'conclusion': 'not_requested'}
    source = observations.get('model_identity_review')
    if source is None:
        raise ValueError('缺少模型身份复核：Agent 须核对仓库与本地权重来源，提供 model_identity_review')
    review = checked_review(source, binding)
    if review['conclusion'] == 'conflict':
        raise DecisionRequired('仓库 ID 与本地模型身份冲突，请确认实际模型；保留原权重，不自动下载或覆盖',
                               {'kind': 'model_identity_conflict', **binding, 'reason': review['reason'], 'review': source})
    if review['conclusion'] != 'match':
        raise ValueError('模型身份尚不能确认一致，Agent 须继续核对来源；必要身份仍缺失时询问用户')
    return {**review, 'review': source}
