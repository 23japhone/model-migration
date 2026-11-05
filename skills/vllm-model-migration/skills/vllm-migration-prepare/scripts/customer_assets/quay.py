"""从官方 Quay 仓库的完整活跃标签清单选择基础版本镜像。"""
from http.client import HTTPException
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPOSITORY = 'quay.io/ascend/vllm-ascend'
TAGS_URL = 'https://quay.io/api/v1/repository/ascend/vllm-ascend/tag/'
NUMBER = r'(0|[1-9][0-9]*)'
VERSION = re.compile(rf'v{NUMBER}\.{NUMBER}\.{NUMBER}(?:rc{NUMBER}|\.post{NUMBER})?')
PAGE_BYTES = 8 * 1024 * 1024


def version_key(tag):
    match = VERSION.fullmatch(tag)
    if not match:
        return None
    major, minor, patch, rc, post = match.groups()
    if rc is not None:
        return int(major), int(minor), int(patch), 0, int(rc)
    if post is not None:
        return int(major), int(minor), int(patch), 2, int(post)
    return int(major), int(minor), int(patch), 1, 0


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('镜像 manifest digest 必须为完整的小写 sha256')
    return value


def fetch_page(number, attempt):
    query = urlencode({'onlyActiveTags': 'true', 'limit': 100, 'page': number})
    request = Request(TAGS_URL + '?' + query, headers={'Accept': 'application/json',
                                                       'User-Agent': 'vllm-model-migration'})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(PAGE_BYTES + 1)
    except (HTTPException, OSError) as error:
        raise ValueError(f'读取 Quay 标签第 {number} 页失败：{error}') from error
    if len(raw) > PAGE_BYTES:
        raise ValueError(f'Quay 标签第 {number} 页超过大小限制')
    (attempt / f'quay-tags-page-{number}.json').write_bytes(raw)
    page = json.loads(raw)
    if not isinstance(page, dict) or type(page.get('page')) is not int or page['page'] != number:
        raise ValueError(f'Quay 标签页码与请求不一致：{number}')
    if not isinstance(page.get('tags'), list) or type(page.get('has_additional')) is not bool:
        raise ValueError(f'Quay 标签第 {number} 页缺少有效标签列表或分页状态')
    if page['has_additional'] and not page['tags']:
        raise ValueError(f'Quay 标签第 {number} 页为空，无法确认分页完整')
    return page


def collect_versions(rows, candidates):
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            raise ValueError('Quay 响应包含无效标签记录')
        tag = row['name']
        version = version_key(tag)
        if version is None:
            continue
        entry = tag, row.get('manifest_digest')
        if version in candidates and candidates[version] != entry:
            raise ValueError('分页期间镜像标签身份不一致：' + tag)
        candidates[version] = entry


def latest(attempt):
    candidates = {}
    number = 1
    while True:
        page = fetch_page(number, attempt)
        collect_versions(page['tags'], candidates)
        if not page['has_additional']:
            break
        number += 1
    if not candidates:
        raise ValueError('官方 Quay 仓库中未找到带版本号的基础镜像')
    tag, index = candidates[max(candidates)]
    index = digest(index)
    return {'reference': REPOSITORY + ':' + tag, 'release': tag, 'tag_manifest_digest': index,
            'release_url': 'https://quay.io/repository/ascend/vllm-ascend/manifest/' + index}
