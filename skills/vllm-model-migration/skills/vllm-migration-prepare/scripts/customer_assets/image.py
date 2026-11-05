"""解析官方稳定发布与目标平台 digest；兼容性仍由官方资料复核。"""
import json
import hashlib
import re
from urllib.request import Request, urlopen

from migration_prepare.process import call
from migration_prepare.results import write_json, safe_text
from .environment import stable_version


def stable_release(attempt):
    url = 'https://api.github.com/repos/vllm-project/vllm-ascend/releases?per_page=100'
    with urlopen(Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'vllm-model-migration'}),
                 timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('官方发布记录过大')
    releases = json.loads(raw)
    usable = [row for row in releases if not row.get('draft') and not row.get('prerelease')
              and stable_version(row.get('tag_name', '').removeprefix('v')) is not None]
    if not usable:
        raise ValueError('官方发布中未找到稳定镜像版本')
    latest = max(usable, key=lambda row: stable_version(row['tag_name'].removeprefix('v')))
    (attempt / 'official-releases.json').write_bytes(raw)
    return {'reference': 'quay.io/ascend/vllm-ascend:' + latest['tag_name'],
            'release': latest['tag_name'], 'release_url': latest['html_url']}


def resolve(reference, platform, attempt):
    if not re.fullmatch(r'linux/(arm64|amd64)(/v[0-9]+)?', platform):
        raise ValueError('镜像平台无效')
    key = hashlib.sha256(json.dumps([reference, platform]).encode()).hexdigest()
    frozen = attempt.parent / ('image-selection-' + key + '.json')
    selected = select(reference, platform, attempt, frozen)
    if 'expected_image_id' in selected or 'expected_digest' in selected:
        return selected
    if not isinstance(selected['reference'], str) or any(c.isspace() for c in selected['reference']):
        raise ValueError('镜像引用无效')
    result = call(['docker', 'manifest', 'inspect', '--verbose', selected['reference']], 60)
    (attempt / 'manifest.stdout.json').write_text(safe_text(result.stdout))
    (attempt / 'manifest.stderr.log').write_text(safe_text(result.stderr))
    if result.returncode:
        raise ValueError('无法解析指定镜像身份；不自动降级到旧镜像')
    raw = json.loads(result.stdout)
    records = raw if isinstance(raw, list) else [raw]
    descriptors = [row['Descriptor'] for row in records]
    matches = [row for row in descriptors if row.get('platform', {}).get('os', '') + '/' +
               row.get('platform', {}).get('architecture', '') == platform]
    if len(matches) != 1:
        raise ValueError('镜像未提供唯一目标平台 manifest')
    selected.update(expected_digest=matches[0]['digest'], platform=platform, source='registry')
    write_json(frozen, selected)
    write_json(attempt / 'image-resolution.json', selected)
    return selected


def select(reference, platform, attempt, frozen):
    if frozen.exists():
        if frozen.is_symlink():
            raise ValueError('镜像选择不能经过链接重定向')
        return json.loads(frozen.read_text())
    if type(reference) is int and reference == -1:
        selected = stable_release(attempt)
    else:
        selected = local(reference, platform, attempt) or {'reference': reference}
    with frozen.open('x') as stream:
        json.dump(selected, stream)
    return selected


def local(reference, platform, attempt):
    if not isinstance(reference, str) or reference.startswith('-') or any(c.isspace() for c in reference):
        raise ValueError('镜像引用无效')
    result = call(['docker', 'image', 'inspect', reference], 30)
    if result.returncode:
        if 'No such image:' in result.stderr or 'No such object:' in result.stderr:
            return None
        raise ValueError('无法核对本地镜像；不是已确认不存在')
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('本地镜像身份不唯一')
    record = rows[0]
    actual = record.get('Os', '') + '/' + record.get('Architecture', '')
    if actual != platform.removesuffix('/v8'):
        raise ValueError('本地镜像与目标平台不一致')
    if not re.fullmatch('sha256:[a-f0-9]{64}', record.get('Id', '')):
        raise ValueError('本地镜像缺少完整 image ID')
    value = {'reference': reference, 'platform': platform, 'source': 'reuse', 'expected_image_id': record['Id']}
    write_json(attempt / 'local-image-identity.json', value)
    return value
