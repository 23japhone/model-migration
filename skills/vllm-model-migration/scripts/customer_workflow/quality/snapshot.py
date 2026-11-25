"""核对固定官方 revision 上的原始题库字节，支持独立文件及发布归档。"""
import hashlib
import io
from contextlib import contextmanager
from pathlib import PurePosixPath
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

from coordination.runtime import sources
from ..state import identity
from .resolution import publish

MAX_BYTES = 512 * 1024 * 1024


def relative(value):
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or p.as_posix() != value or not p.parts or '\\' in value:
        raise ValueError('官方仓库文件路径无效')
    return quote(value, safe='/')


def raw_url(repository, revision, path):
    suffix = '/' + revision + '/' + relative(path)
    if repository.startswith('https://github.com/'):
        return repository.replace('https://github.com/', 'https://raw.githubusercontent.com/', 1) + suffix
    return repository + '/resolve' + suffix


def contents(url, source):
    if 'content_sources' in source:
        captured = sources.bound(source['content_sources'][url])
        if captured['url'] != url:
            raise ValueError('原始题库资料的不可变 URL 不同')
        ref = captured['payload']
        body = sources.stable_bytes(ref['path'], MAX_BYTES)
        if hashlib.sha256(body).hexdigest() != ref['sha256']:
            raise ValueError('已获取的官方题库内容发生变化')
        return body
    with urlopen(Request(url, headers={'User-Agent': 'vllm-model-migration'}), timeout=30) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('官方题库文件超过大小限制')
    return raw


@contextmanager
def official_zip(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            yield archive
    except zipfile.BadZipFile as error:
        raise ValueError('官方题库的 ZIP 归档无效或校验失败') from error


def archive_digests(raw, mapping):
    with official_zip(raw) as archive:
        names = archive.namelist()
        result = {}
        for local, member in mapping.items():
            relative(member)
            if names.count(member) != 1 or archive.getinfo(member).file_size > MAX_BYTES:
                raise ValueError('官方数据归档缺少唯一可核验的文件：' + member)
            digest = hashlib.sha256()
            with archive.open(member) as file:
                for block in iter(lambda: file.read(1048576), b''):
                    digest.update(block)
            result[local] = digest.hexdigest()
        return result


def verify_upstream(binding, source, root):
    files = binding['files']
    cache = root / 'upstream' / (identity(binding) + '.json')
    if cache.exists():
        saved = sources.read(cache)
        if saved['files'] != files or saved['revision'] != binding['revision']:
            raise ValueError('已冻结题库的来源记录损坏')
        return
    mapping = binding.get('upstream_paths', {name: name for name in files})
    if set(mapping) != set(files):
        raise ValueError('官方快照原文件路径映射不完整')
    repo, revision = binding['repository'], binding['revision']
    if binding.get('archive'):
        url = raw_url(repo, revision, binding['archive'])
        actual = archive_digests(contents(url, source), mapping)
    else:
        actual = {logical: hashlib.sha256(contents(raw_url(repo, revision, name), source)).hexdigest()
                  for logical, name in mapping.items()}
    if actual != files:
        raise ValueError('官方固定 revision 实际字节与本地数据清单不符')
    publish(cache, {'repository': repo, 'revision': revision, 'files': actual})
