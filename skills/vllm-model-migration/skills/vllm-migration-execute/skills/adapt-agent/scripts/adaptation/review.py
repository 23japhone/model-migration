"""按当前源码身份核对外部定向测试；不执行声明中的 command。"""
from pathlib import Path

from .evidence import bound, stable


def record(reference, fingerprints, config_sha256):
    value = bound(reference)
    name = value['id']
    if not isinstance(name, str) or not name.strip():raise ValueError('定向检查 id 无效')
    if value.get('source_fingerprints') != fingerprints or value.get('config_sha256') != config_sha256:
        raise ValueError('定向检查未绑定当前源码/config')
    if value.get('status') not in ('pass', 'fail', 'review_required'):raise ValueError('定向检查结论无效')
    if not isinstance(value.get('summary'), str) or not value['summary'].strip():raise ValueError('定向检查摘要缺失')
    artifacts(value)
    return name, value


def artifacts(value):
    command = value.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s for s in command):
        raise ValueError('定向检查缺少实际 argv 记录')
    artifacts = value.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:raise ValueError('定向检查缺少原始证据')
    for item in artifacts:
        if stable(Path(item['path']))[1] != item['sha256']:raise ValueError('定向原始证据 SHA256 不一致')


def inspect(items, required, fingerprints, config_sha256):
    records, errors = {}, []
    for reference in items:
        try:
            name, value = record(reference, fingerprints, config_sha256)
            if name in records:raise ValueError('定向检查 id 重复')
            records[name] = {'status': value['status'], 'reference': reference, 'record': value}
            if value['status'] != 'pass':errors.append(name + '：检查未通过')
        except (OSError, ValueError, KeyError, TypeError) as error:errors.append(str(error))
    missing = set(required) - records.keys()
    if missing:errors.append('缺少必需定向检查：' + ','.join(sorted(missing)))
    return {'checks': records, 'errors': errors, 'execution': '外部按 Skill 步骤执行；本脚本仅验证绑定和证据完整性'}
