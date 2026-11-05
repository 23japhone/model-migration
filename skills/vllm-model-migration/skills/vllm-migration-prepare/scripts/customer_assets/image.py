"""冻结客户选择的镜像，核验对应目标平台的 manifest digest。"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
import sys

from migration_prepare.process import call
from migration_prepare.results import write_json, safe_text
from . import quay


@contextmanager
def selection_lock(path):
    # 独立锁跨越标签选择和平台核验；原子替换冻结文件时不替换锁本身。
    if path.is_symlink():
        raise ValueError('镜像选择锁不能经过链接重定向')
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'r+b') as lock:
        if not os.path.samestat(os.fstat(lock.fileno()), path.lstat()):
            raise ValueError('镜像选择锁身份发生变化')
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def report_selection_state(is_frozen):
    # 只在持有选择锁、已确证是否发布选版文件的区间标记失败回执。
    try:
        yield
    except (Exception, KeyboardInterrupt) as error:
        error.selection_frozen = is_frozen
        raise


def resolve(reference, platform, attempt):
    if not re.fullmatch(r'linux/(arm64|amd64)(/v[0-9]+)?', platform):
        raise ValueError('镜像平台无效')
    key = hashlib.sha256(json.dumps([reference, platform]).encode()).hexdigest()
    frozen = attempt.parent / ('image-selection-' + key + '.json')
    with selection_lock(frozen.with_suffix('.lock')):
        selected = select(reference, platform, attempt, frozen)
        with report_selection_state(True):
            if 'expected_image_id' in selected or 'expected_digest' in selected:
                return selected
            record = platform_manifest(selected, platform, attempt)
            selected.update(expected_digest=quay.digest(record['Descriptor'].get('digest')),
                            platform=platform, source='registry')
            if 'tag_manifest_digest' in selected:
                selected.update(config_identity(record))
            write_json(frozen, selected)
            write_json(attempt / 'image-resolution.json', selected)
            return selected


def platform_manifest(selected, platform, attempt):
    target = selected['reference']
    if not isinstance(target, str) or target.startswith('-') or any(c.isspace() for c in target):
        raise ValueError('镜像引用无效')
    if 'tag_manifest_digest' in selected:
        target = quay.REPOSITORY + '@' + quay.digest(selected['tag_manifest_digest'])
    result = call(['docker', 'manifest', 'inspect', '--verbose', target], 60)
    (attempt / 'manifest.stdout.json').write_text(safe_text(result.stdout))
    (attempt / 'manifest.stderr.log').write_text(safe_text(result.stderr))
    if result.returncode:
        raise ValueError('无法解析指定镜像身份；不自动降级到旧镜像')
    return matching_record(json.loads(result.stdout), platform)


def matching_record(raw, platform):
    records = raw if isinstance(raw, list) else [raw]
    matches = []
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('Descriptor'), dict):
            raise ValueError('Docker 镜像 manifest 缺少有效 Descriptor')
        descriptor = row['Descriptor']
        if matches_platform(descriptor.get('platform'), platform):
            matches.append(row)
    if len(matches) != 1:
        raise ValueError('镜像未提供唯一目标平台 manifest')
    return matches[0]


def config_identity(record):
    ids = set()
    for key in ('SchemaV2Manifest', 'OCIManifest'):
        manifest = record.get(key)
        if manifest is None:
            continue
        if not isinstance(manifest, dict):
            raise ValueError('镜像平台 manifest 内容无效：' + key)
        config = manifest.get('config')
        if config is None:
            continue
        if not isinstance(config, dict):
            raise ValueError('镜像平台 manifest.config 无效')
        if 'digest' in config:
            ids.add(quay.digest(config['digest']))
    if len(ids) > 1:
        raise ValueError('镜像平台配置给出了冲突的 image ID')
    return {'expected_image_id': ids.pop()} if ids else {}


def matches_platform(actual, wanted):
    if not isinstance(actual, dict):
        return False
    system, architecture, *variant = wanted.split('/')
    if (actual.get('os'), actual.get('architecture')) != (system, architecture):
        return False
    # 镜像准备契约只将 arm64 与 arm64/v8 视为同一平台。
    default_variant = 'v8' if architecture == 'arm64' else None
    wanted_variant = variant[0] if variant else default_variant
    return (actual.get('variant') or default_variant) == wanted_variant


def select(reference, platform, attempt, frozen):
    try:
        mode = frozen.lstat().st_mode
    except FileNotFoundError:
        with report_selection_state(False):
            if type(reference) is int and reference == -1:
                selected = quay.latest(attempt)
            else:
                selected = local(reference, platform, attempt) or {'reference': reference}
    else:
        if not stat.S_ISREG(mode):
            raise ValueError('镜像冻结选择必须为普通文件且不能经过链接重定向')
        stored = json.loads(frozen.read_text())
        if not isinstance(stored, dict) or not isinstance(stored.get('reference'), str) or not stored['reference']:
            raise ValueError('镜像冻结选择缺少有效引用')
        return stored
    # 发布中发生的存储错误保留未知状态，不能据此允许更换平台。
    write_json(frozen, selected)
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
