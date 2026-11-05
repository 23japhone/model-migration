"""从固定 registry index 核对本地缓存所用的平台 manifest 与镜像配置。"""
import json


def canonical_platform(value):
    return 'linux/arm64' if value == 'linux/arm64/v8' else value


def platform_record(records, platform):
    if not isinstance(records, list):
        raise ValueError('固定 digest 的 Docker manifest 不是镜像索引')
    matches = []
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('Descriptor'), dict):
            raise ValueError('镜像索引记录缺少 Descriptor')
        descriptor = row['Descriptor']
        target = descriptor.get('platform')
        if not isinstance(target, dict):
            continue
        name = f"{target.get('os')}/{target.get('architecture')}"
        if target.get('variant'):
            name += f"/{target['variant']}"
        if canonical_platform(name) == canonical_platform(platform):
            matches.append(row)
    if len(matches) != 1:
        raise ValueError('镜像索引未提供唯一目标平台 manifest')
    return matches[0]


def inspect_cache(docker, record, request, repository):
    index = repository + '@' + request['expected_index_digest']
    child = repository + '@' + request['expected_digest']
    actual = record.get('RepoDigests') or []
    if child in actual or index not in actual:
        return None
    raw = docker.invoke(['manifest', 'inspect', '--verbose', index])
    row = platform_record(json.loads(raw), request['platform'])
    if row['Descriptor'].get('digest') != request['expected_digest']:
        raise ValueError('索引的目标平台 manifest digest 与预期不一致')
    manifest = row.get('SchemaV2Manifest') or row.get('OCIManifest')
    config = manifest.get('config') if isinstance(manifest, dict) else None
    if not isinstance(config, dict) or not config.get('digest') or config['digest'] != record.get('Id'):
        raise ValueError('索引的平台配置 digest 与本地 image ID 不一致')
    return {'reference': index, 'manifest_digest': row['Descriptor']['digest'],
            'platform': request['platform'], 'config_digest': config['digest']}
