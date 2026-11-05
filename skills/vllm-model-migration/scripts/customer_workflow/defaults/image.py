"""将客户缺省镜像绑定到本次公开镜像发现回执，保留 Agent 的配方观察。"""
import re

from coordination.runtime import sources
from .. import effects
from . import previous_image


STEP = 'image_resolution'


def discover(session, task, observations, work_root):
    if task['environment']['image_name'] != -1 or session.ready('preparation'):
        return
    platform = observations['target']['platform']
    if not isinstance(platform, str) or not re.fullmatch(r'linux/(?:arm64|amd64)(?:/v[0-9]+)?', platform):
        raise ValueError('target.platform 须提供有效的目标 Linux 镜像平台')
    request = {'operation': 'image', 'reference': -1, 'platform': platform}
    row = session.value['steps'].get(STEP, {})
    if already_resolved(row, request):
        # 上一次可能因提交了冲突平台而阻塞；原选择回执仍可用于相同请求。
        session.step(STEP, 'completed')
        return
    if not row.get('request'):
        restored = previous_image.recover(session, request, work_root)
        if restored is not None:
            session.step(STEP, 'completed', request=session.artifact(STEP, request),
                         receipt=session.artifact('recovered-image', restored))
            return
    session.step(STEP, 'running', request=session.artifact(STEP, request), receipt=None)
    effects.operation(session, STEP, 'vllm-migration-prepare', request, work_root)


def already_resolved(row, request):
    prior = sources.bound(row['receipt']) if row.get('receipt') else {}
    if row.get('request') and sources.bound(row['request']) != request and prior.get('selection_frozen') is not False:
        raise ValueError('已选择或正在解析的镜像平台发生变化，须使用新 run-id')
    return prior.get('status') == 'completed'


def selection(session, platform):
    report = session.ready(STEP)
    if not report:
        diagnostic = session.value['steps'].get(STEP, {}).get('diagnostic') or {}
        reason = diagnostic.get('message', '等待公开镜像发现回执')
        raise ValueError('缺省镜像尚未就绪：' + reason)
    expected = {'operation': 'image', 'reference': -1, 'platform': platform}
    if report['inputs'] != expected or report['result']['platform'] != platform:
        raise ValueError('缺省镜像回执与当前平台不一致')
    return report


def image_request(task, observations, session):
    supplied = observations['image_request']
    if not isinstance(supplied, dict):
        raise ValueError('image_request 须为对象')
    explicit = task['environment']['image_name']
    if isinstance(explicit, str):
        if supplied['reference'] != explicit:
            raise ValueError('镜像解析结果改变了客户显式镜像')
        return supplied
    target = observations['target']
    report = selection(session, target['platform'])
    if report['operation'] == 'recover_image_request':
        return previous_image.bind(report, target, supplied)
    return registry_request(report['result'], target, supplied)


def registry_identity(selected, hardware):
    if selected['source'] != 'registry':
        raise ValueError('公开缺省镜像发现回执须固定 registry 来源')
    identity = {key: selected[key] for key in ('source', 'platform', 'reference', 'expected_digest')}
    identity['hardware'] = hardware
    if 'expected_image_id' in selected:
        identity['expected_image_id'] = selected['expected_image_id']
    if 'tag_manifest_digest' in selected:
        identity['expected_index_digest'] = selected['tag_manifest_digest']
    return identity


def registry_request(selected, target, supplied):
    identity = registry_identity(selected, target['hardware'])
    check_recipe(supplied['recipe'], selected, target)
    if 'reference' in supplied and not matches_reference(supplied['reference'], selected):
        raise ValueError('image_request.reference 与本次官方镜像选择冲突')
    for key in ('source', 'platform', 'expected_digest', 'expected_image_id', 'expected_index_digest', 'hardware'):
        if key in supplied and (key not in identity or supplied[key] != identity[key]):
            raise ValueError('image_request.' + key + ' 与本次官方镜像或目标不一致')
    return {**supplied, **identity}


def matches_reference(reference, selected):
    repository = selected['reference'].rsplit(':', 1)[0]
    return reference in (selected['reference'], repository + '@' + selected['expected_digest'])


def check_recipe(recipe, selected, target):
    if not matches_reference(recipe['source_ref'], selected):
        raise ValueError('recipe.source_ref 与官方镜像的 tag 或平台 digest 不一致')
    for key in ('vllm', 'vllm_ascend'):
        if not isinstance(recipe[key], str) or recipe[key] != target[key]:
            raise ValueError('recipe.' + key + ' 与阶段 target.' + key + ' 不一致')
    if recipe['vllm_ascend'].split('+', 1)[0] != selected['release'].removeprefix('v'):
        raise ValueError('recipe.vllm_ascend 与选中镜像发布版本不一致')
