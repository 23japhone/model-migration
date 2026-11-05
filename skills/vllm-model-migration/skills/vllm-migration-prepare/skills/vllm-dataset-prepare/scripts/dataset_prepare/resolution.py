"""请求路径缺失时读取有官方资料依据的默认目录，缺失后才使用原下载器。"""
from pathlib import Path

from . import sources
from .assets import file_hash, inspect_dataset


def exists(path):
    # 权限、I/O 等错误必须保留；只有明确不存在才允许进入下载分支。
    try:
        Path(path).stat()
    except FileNotFoundError:
        return False
    return True


def verify_evidence(reference):
    path = Path(reference['path'])
    if path.resolve() != path or not path.is_file() or file_hash(path) != reference['sha256']:
        raise ValueError('default_local_evidence_changed: 官方默认目录资料路径或摘要变化：' + str(path))


def read_default(request, resolution):
    candidate = Path(resolution['default_path'])
    if candidate.resolve() != candidate:
        raise ValueError('default_local_redirected: 官方默认目录经链接重定向：' + str(candidate))
    if not exists(candidate):
        resolution['decision'] = 'download_requested'
        return None
    if request.get('expected_sha256'):
        raise ValueError('默认本地文件不能验证下载包 expected_sha256；请核对文件清单 expected_identity，或不启用 default_local')
    try:
        asset = inspect_dataset(request, candidate)
    except (OSError, ValueError) as error:
        raise ValueError('default_local_unreadable: 默认目录存在但不能复用：' + str(candidate) + '；' +
                         type(error).__name__ + ': ' + str(error)) from error
    resolution.update(decision='reused_default', actual_path=asset['path'], identity=asset['identity'])
    return str(candidate)


def prepare_source(request, on_snapshot):
    candidate = request.get('default_local')
    if candidate is None or exists(request['path']):
        return sources.prepare_source(request, on_snapshot)
    resolution = {'requested_path': request['path'], 'download_path': request['path'],
                  'default_path': candidate['path'], 'evidence': candidate['evidence'], 'decision': 'checking_default'}
    on_snapshot({'kind': 'source_resolution', 'resolution': resolution})
    verify_evidence(candidate['evidence'])
    path = read_default(request, resolution)
    if path is not None:
        source = {'kind': 'local', 'upstream_status': 'unverified', 'resolution': resolution}
        on_snapshot(source)
        return path, source, 'reused'
    def snapshot(source):
        on_snapshot({**source, 'resolution': resolution})
    snapshot({'kind': 'source_resolution'})
    path, source, operation = sources.prepare_source(request, snapshot)
    return path, {**source, 'resolution': resolution}, operation


def verify_resolution(source, asset):
    resolution = source.get('resolution')
    if resolution is None:
        return
    verify_evidence(resolution['evidence'])
    if resolution['decision'] == 'reused_default':
        identity = asset.get('provenance', {}).get('source_identity', asset['identity'])
        if identity != resolution['identity']:
            raise ValueError('default_local_identity_changed: 复用目录在准备期间变化')
