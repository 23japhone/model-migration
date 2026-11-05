"""从旧客户准备台账恢复固定镜像请求，已完成资产须同时核验原始回执。"""
from pathlib import Path
import re

from coordination.runtime import receipts, sources


def recover(session, request, work_root):
    prior = (session.previous or {}).get('steps', {}).get('preparation', {})
    if not (prior.get('request') or prior.get('receipt')):
        return None
    proof = {'stage': prior.get('receipt'), 'stage_request': prior.get('request'),
             'run_root': str(Path(work_root) / session.value['run_id'])}
    result, status = verified_inputs(proof)
    if result['platform'] != request['platform']:
        raise ValueError('历史镜像请求与当前平台不同，须使用新 run-id')
    return {'status': 'completed', 'operation': 'recover_image_request', 'inputs': request,
            'result': result, 'historical_preparation': proof, 'historical_image_status': status}


def verified_inputs(proof):
    try:
        return read_inputs(proof, Path(proof['run_root']))
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ValueError('旧任务镜像准备证据无法核实，须使用新 run-id：' + str(error)) from error


def read_inputs(proof, root):
    stage_source = proof['stage']
    stage = sources.bound(stage_source)
    request = sources.bound(proof['stage_request'])
    receipts.common(stage, 'vllm-migration-prepare')
    if (stage['run_id'] != root.name or stage['evidence']['manifest'] != stage_source['path']
            or not Path(stage_source['path']).is_relative_to(root)):
        raise ValueError('历史阶段回执与任务身份不一致')
    if (stage['inputs']['tasks']['image']['request'] != request['tasks']['image']['request']
            or stage['inputs']['target'] != request['target']):
        raise ValueError('历史阶段输入与客户准备请求不一致')
    row = stage['tasks']['image']
    frozen = row['request']
    check_frozen_source(frozen, request['tasks']['image']['request'])
    check_original(row, frozen, root)
    return frozen, row['status']


def check_frozen_source(frozen, path):
    original = sources.read(path)
    if not isinstance(frozen, dict):
        raise ValueError('阶段镜像请求不是可核验的对象')
    for key in ('source', 'reference', 'expected_digest', 'expected_image_id', 'hardware', 'platform', 'recipe'):
        if frozen.get(key) != original.get(key):
            raise ValueError('阶段镜像 ' + key + ' 与旧客户准备输入不一致')
    ids = [frozen[key] for key in ('expected_digest', 'expected_image_id') if key in frozen]
    if not ids or any(not re.fullmatch(r'sha256:[a-f0-9]{64}', str(value)) for value in ids):
        raise ValueError('历史镜像请求缺少有效固定身份')


def check_original(row, frozen, root):
    # 旧阶段的子任务预检或拉取也可能失败；只恢复可核实的请求，不提升资产状态。
    if not row.get('manifest') and row['status'] == 'blocked':
        return
    if not Path(row['manifest']).is_relative_to(root):
        raise ValueError('历史镜像回执不属于本任务运行目录')
    child = receipts.child(row, 'vllm-image-prepare', root.name)
    if child['evidence']['manifest'] != row['manifest'] or row['status'] != child['status']:
        raise ValueError('历史镜像清单路径或状态与阶段记录不一致')
    if row['status'] == 'completed':
        if row['conclusions']['assets'] != 'pass' or child['asset'] != row['asset']:
            raise ValueError('历史镜像资产与阶段结论不一致')
        check_identity(frozen, child['asset'])


def check_identity(frozen, asset):
    image_id = asset['image_id']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(image_id)) or frozen.get('expected_image_id', image_id) != image_id:
        raise ValueError('历史镜像 image ID 不一致')
    digest = frozen.get('expected_digest')
    if digest is None:
        if frozen.get('expected_image_id') != image_id:
            raise ValueError('历史镜像输入缺少固定身份')
        return
    reference = frozen.get('reference', frozen['recipe']['source_ref'])
    if not isinstance(reference, str):
        raise ValueError('历史镜像引用无效')
    if reference.startswith('sha256:'):
        reference = frozen['recipe']['source_ref']
    address = reference.split('@', 1)[0]
    prefix, slash, name = address.rpartition('/')
    pinned = prefix + slash + name.split(':', 1)[0] + '@' + digest
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) or pinned not in asset['repo_digests']:
        raise ValueError('历史镜像 repository digest 不一致')


def bind(report, target, supplied):
    frozen, status = verified_inputs(report['historical_preparation'])
    if frozen != report['result'] or status != report['historical_image_status']:
        raise ValueError('历史准备镜像回执改变，须使用新 run-id')
    expected = {key: frozen[key] for key in ('hardware', 'platform')}
    expected.update({key: frozen['recipe'][key] for key in ('vllm', 'vllm_ascend')})
    for key, value in expected.items():
        if target[key] != value:
            raise ValueError('target.' + key + ' 与历史镜像请求不一致')
    for key, value in supplied.items():
        if key not in frozen or frozen[key] != value:
            raise ValueError('image_request.' + key + ' 与历史镜像请求不一致')
    return frozen
