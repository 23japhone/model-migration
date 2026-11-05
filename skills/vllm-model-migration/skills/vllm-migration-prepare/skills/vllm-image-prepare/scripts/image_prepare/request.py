"""在访问 Docker 或写证据前检查镜像请求。"""
from pathlib import Path
import re
from urllib.parse import urlsplit

FIELDS = {'source', 'reference', 'archive', 'archive_sha256', 'expected_image_id', 'expected_digest',
          'platform', 'hardware', 'recipe', 'timeout_seconds'}


def digest(value, prefix=True):
    pattern = r'sha256:[a-f0-9]{64}' if prefix else r'[a-f0-9]{64}'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('镜像或归档摘要必须是完整的小写 SHA256')
    return value


def reference(value):
    if isinstance(value, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', value):
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._:/@-]*', value):
        raise ValueError('镜像引用必须是明确的名称、tag、digest 或完整 image ID')
    if '@' in value:
        repo, hashed = value.rsplit('@', 1)
        digest(hashed)
        if not repo or '@' in repo:
            raise ValueError('镜像 repository 无效')
    elif ':' not in value.rsplit('/', 1)[-1]:
        raise ValueError('必须显式选择版本 tag 或 digest，不能隐式使用 latest')
    return value


def validate_recipe(value, hardware):
    fields = {'source_ref', 'vllm', 'vllm_ascend', 'hardware_families', 'evidence'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('recipe 必须记录 vLLM、vLLM-Ascend、上游来源、硬件系列与官方资料')
    source = reference(value['source_ref'])
    repo = source.split('@')[0].rsplit(':', 1)[0] if '@' not in source else source.split('@')[0]
    if not repo.endswith('/vllm-ascend'):
        raise ValueError('配方必须指向 vLLM-Ascend 镜像来源')
    for name in ('vllm', 'vllm_ascend'):
        if not isinstance(value[name], str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+_-]*', value[name]):
            raise ValueError('配方必须明确版本，不能使用空值或 latest')
    families = value['hardware_families']
    if not isinstance(families, list) or not families or any(not isinstance(x, str) or not x.strip() for x in families):
        raise ValueError('配方必须列出适用的硬件系列')
    if hardware['family'] not in families:
        raise ValueError('目标硬件系列不在所选镜像配方的适用条件内')
    validate_evidence(value['evidence'])
    return dict(value)


def validate_evidence(urls):
    if not isinstance(urls, list) or not urls:
        raise ValueError('配方必须附带已复核的官方版本与硬件资料链接')
    for url in urls:
        parsed = urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            raise ValueError('资料链接必须为不包含凭据或查询参数的 HTTPS 链接')


def normalize_request(value, base=None):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError('请求只能包含契约字段，不能写入凭据')
    if value.get('source') not in ('reuse', 'registry', 'tar'):
        raise ValueError('source 必须为 reuse/registry/tar')
    result = dict(value)
    if not re.fullmatch(r'linux/(arm64|amd64)(/v[0-9]+)?', str(value.get('platform', ''))):
        raise ValueError('必须指定昇腾目标的 Linux 镜像平台')
    hardware = value.get('hardware')
    if not isinstance(hardware, dict) or set(hardware) != {'name', 'family'} or any(
            not isinstance(x, str) or not x.strip() for x in hardware.values()):
        raise ValueError('hardware 必须明确芯片名称和产品系列，不按设备数量推断')
    result['recipe'] = validate_recipe(value.get('recipe'), hardware)
    result['timeout_seconds'] = value.get('timeout_seconds', 600)
    if type(result['timeout_seconds']) is not int or not 1 <= result['timeout_seconds'] <= 3600:
        raise ValueError('timeout_seconds 必须为 1 至 3600 的整数')
    normalize_identity(result, base)
    return result


def normalize_identity(result, base):
    for name in ('expected_image_id', 'expected_digest'):
        if name in result:
            digest(result[name])
    if not {'expected_image_id', 'expected_digest'} & result.keys():
        raise ValueError('必须提供 expected_image_id 或 expected_digest 固定资产身份')
    if result['source'] == 'tar':
        normalize_archive(result, base)
    elif {'archive', 'archive_sha256'} & result.keys():
        raise ValueError('非 tar 来源不能提供归档字段')
    if result['source'] != 'tar' or 'reference' in result:
        result['reference'] = reference(result.get('reference'))
    if result['source'] == 'registry' and 'expected_digest' not in result:
        raise ValueError('registry 拉取必须固定 expected_digest，避免 tag 漂移')
    ref = result.get('reference', '')
    if '@' in ref and 'expected_digest' in result and ref.rsplit('@', 1)[1] != result['expected_digest']:
        raise ValueError('reference 与 expected_digest 冲突')


def normalize_archive(result, base):
    if 'expected_image_id' not in result or not isinstance(result.get('archive'), str) or not result['archive'].strip():
        raise ValueError('tar 加载必须提供归档路径和 expected_image_id')
    path = Path(result['archive']).expanduser()
    result['archive'] = str((Path(base or Path.cwd()) / path).resolve())
    if 'archive_sha256' in result:
        digest(result['archive_sha256'], prefix=False)
