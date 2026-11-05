"""通过 Docker CLI 获取实际身份，必要时拉取或加载镜像。"""
import json
import shutil
import subprocess

from .archive import file_digest, inspect_archive
from . import index_manifest


def repository(ref):
    value = ref.split('@', 1)[0]
    head, tail = value.rsplit('/', 1) if '/' in value else ('', value)
    tail = tail.split(':', 1)[0]
    return head + '/' + tail if head else tail


class Docker:
    """只读检查 daemon、镜像和固定 manifest；按请求执行 pull 或 load。"""

    def __init__(self, timeout, journal):
        self.executable = shutil.which('docker')
        if self.executable is None:
            raise ImportError('执行环境未找到 Docker CLI')
        self.timeout = timeout
        self.journal = journal

    def command(self, argv):
        try:
            result = subprocess.run([self.executable, *argv], capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            self.journal({'argv': argv, 'status': 'timeout'})
            raise RuntimeError('Docker CLI 超时，daemon 侧操作可能仍在执行；复查实际状态后再重试') from error
        self.journal({'argv': argv, 'returncode': result.returncode, 'stderr': result.stderr})
        return result

    def invoke(self, argv):
        result = self.command(argv)
        if result.returncode:
            raise RuntimeError('Docker 命令失败：' + result.stderr.strip())
        return result.stdout

    def inspect(self, ref, missing_ok=False):
        result = self.command(['image', 'inspect', ref])
        if result.returncode:
            if missing_ok and ('No such image:' in result.stderr or 'No such object:' in result.stderr):
                return None
            raise RuntimeError('无法检查 Docker 镜像：' + result.stderr.strip())
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError('Docker inspect 未返回一份明确的镜像')
        return rows[0]

    def daemon(self):
        info = json.loads(self.invoke(['info', '--format', '{{json .}}']))
        return {name: info.get(name) for name in ('Name', 'ID', 'OSType', 'Architecture', 'ServerVersion')}


def image_identity(record, request, proof=None):
    actual = record.get('Id')
    from .request import digest
    digest(actual)
    if request.get('expected_image_id') and request['expected_image_id'] != actual:
        raise ValueError('实际 image ID 与预期不一致，保留原镜像和标签')
    platform = '/'.join(str(record.get(k, '')) for k in ('Os', 'Architecture'))
    if record.get('Variant'):
        platform += '/' + record['Variant']
    wanted = request['platform']
    if platform != wanted and {platform, wanted} != {'linux/arm64', 'linux/arm64/v8'}:
        raise ValueError('实际镜像平台与目标不一致')
    digests = record.get('RepoDigests') or []
    tags = record.get('RepoTags') or []
    if not isinstance(digests, list) or not isinstance(tags, list):
        raise ValueError('Docker 镜像标签或 RepoDigests 格式无效')
    checked = verified_digests(digests, actual, proof)
    matched = verify_source(checked, request)
    return {'image_id': actual, 'platform': platform, 'repo_tags': tags, 'repo_digests': digests,
            'size': record.get('Size'), 'source_ref': request['recipe']['source_ref'],
            'source_status': 'registry_record' if matched else 'declared_recipe', 'recipe': request['recipe']}


def verified_digests(digests, image_id, proof):
    if proof is None:
        return digests
    if proof['reference'] not in digests or proof['config_digest'] != image_id:
        raise ValueError('已核验的 index 或本地镜像身份在复查期间变化')
    repo = repository(proof['reference'])
    return [*digests, repo + '@' + proof['manifest_digest']]


def verify_source(digests, request):
    expected = request.get('expected_digest')
    ref = request.get('reference')
    if not ref or ref.startswith('sha256:'):
        ref = request['recipe']['source_ref']
    source_repo = repository(ref)
    if expected and source_repo + '@' + expected not in digests:
        raise ValueError('实际 repository digest 与预期不一致')
    return verify_origin(digests, request['recipe']['source_ref'])


def verify_origin(digests, source_ref):
    origin_repo = repository(source_ref)
    records = [x for x in digests if isinstance(x, str) and x.startswith(origin_repo + '@sha256:')]
    if '@' in source_ref and records and source_ref not in records:
        raise ValueError('配方固定的来源 digest 与实际镜像来源不一致')
    return bool(records)


def check_daemon(daemon, request):
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(daemon.get('Architecture'), daemon.get('Architecture'))
    if daemon.get('OSType') != 'linux' or request['platform'].split('/')[1] != arch:
        raise ValueError('所连 Docker daemon 的平台与指定目标不一致')


def prepare_tar(request, docker):
    info = inspect_archive(request['archive'], request['expected_image_id'], request.get('archive_sha256'))
    ref = request.get('reference')
    if ref and ref != info['image_id'] and ref not in info['tags']:
        raise ValueError('请求的镜像引用不在归档标签或 image ID 中，未执行加载')
    if info['platform'] != request['platform'] and {info['platform'], request['platform']} != {'linux/arm64', 'linux/arm64/v8'}:
        raise ValueError('tar 镜像平台与目标不一致')
    for tag in info['tags']:
        existing = docker.inspect(tag, missing_ok=True)
        if existing is not None and existing.get('Id') != info['image_id']:
            raise ValueError(f'归档标签已指向另一份镜像，禁止覆盖：{tag}')
    existing = docker.inspect(info['image_id'], missing_ok=True)
    if existing is not None:
        return existing, 'reused', info
    if file_digest(request['archive']) != info['sha256']:
        raise ValueError('tar 在加载前变化，未执行加载')
    docker.invoke(['image', 'load', '--input', request['archive']])
    record = docker.inspect(info['image_id'])
    if file_digest(request['archive']) != info['sha256']:
        raise ValueError('tar 在加载期间变化；已加载内容需复核，不删除镜像')
    return record, 'loaded', info


def prepare_registry(request, docker):
    ref = request['reference']
    existing = docker.inspect(ref, missing_ok=True)
    if existing is not None:
        return existing, 'reused', ref
    if request['source'] == 'reuse':
        raise ValueError('请求复用的镜像在目标 Docker 中不存在')
    if '/' not in ref or ref.startswith('sha256:'):
        raise ValueError('registry 来源必须使用明确的 registry/repository 引用')
    repo = repository(ref)
    pinned = repo + '@' + request['expected_digest']
    candidates = [pinned]
    index = request.get('expected_index_digest')
    if index and index != request['expected_digest']:
        candidates.append(repo + '@' + index)
    for candidate in candidates:
        if candidate == ref:
            continue
        cached = docker.inspect(candidate, missing_ok=True)
        if cached is not None:
            return cached, 'reused', candidate
    docker.invoke(['image', 'pull', '--platform', request['platform'], pinned])
    return docker.inspect(pinned), 'pulled', pinned


def prepare_image(request, docker):
    daemon = docker.daemon()
    check_daemon(daemon, request)
    if request['source'] == 'tar':
        record, operation, archive = prepare_tar(request, docker)
        ref = request.get('reference')
    else:
        record, operation, ref = prepare_registry(request, docker)
        archive = None
    proof = None
    if operation == 'reused' and request.get('expected_index_digest'):
        proof = index_manifest.inspect_cache(docker, record, request, repository(request['reference']))
    asset = image_identity(record, request, proof)
    if proof:
        asset['index_manifest'] = proof
    stable = docker.inspect(asset['image_id'])
    if image_identity(stable, request, proof)['image_id'] != asset['image_id']:
        raise ValueError('镜像在核验期间变化')
    if operation == 'reused' and ref:
        selected = docker.inspect(ref, missing_ok=True)
        if selected is None or selected.get('Id') != asset['image_id']:
            raise ValueError('镜像引用在核验期间变化')
    return {**asset, 'operation': operation, 'daemon': daemon, 'hardware': request['hardware'], 'archive': archive}
