"""只读绑定 API 进程环境快照，作为 CANN 路径的补充来源。"""
import hashlib
import json

from coordination.request import fields, reference
from coordination.runtime import sources
from .contracts import absolute, integer, require

CANN_KEYS = ('ASCEND_HOME_PATH', 'ASCEND_TOOLKIT_HOME')


def process_records(source):
    """兼容原采集器的数组格式，不重写原快照或放宽其他 JSON 来源契约。"""
    reference(source)
    raw = sources.stable_bytes(source['path'], 64*1024*1024)
    require(hashlib.sha256(raw).hexdigest() == source['sha256'], '进程环境来源 SHA256 不一致')
    records = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=sources.pairs,
                         parse_constant=sources.invalid_constant)
    require(isinstance(records, list) and records and all(isinstance(row, dict) for row in records),
            '进程环境快照须为非空对象数组')
    return records


def process_identity(value):
    fields(value, ('pid', 'start_ticks', 'pgid'))
    for key in ('pid', 'pgid'):
        integer(value[key], '进程身份 '+key)
    require(isinstance(value['start_ticks'], str) and value['start_ticks'].isascii()
            and value['start_ticks'].isdigit(), '进程身份缺少启动标识')


def environment(source, original, handoff):
    binding = sources.bound(source)
    fields(binding, ('schema_version', 'deployment', 'processes'), ('path_checks',))
    require(binding['schema_version'] == '1.0', '进程环境绑定仅支持 schema_version=1.0')
    require(sources.bound(binding['deployment']) == handoff, '进程环境来源与本次部署交接不一致')
    workers = handoff.get('workers')
    require(isinstance(workers, dict), '部署缺少 API 监听进程身份')
    listeners = workers.get('listener_identities')
    require(isinstance(listeners, list) and listeners, '部署缺少 API 监听进程身份')
    for identity in listeners:
        process_identity(identity)
    records = process_records(binding['processes'])
    selected = [row for row in records if row.get('identity') in listeners]
    require(len(selected) == 1, '进程环境快照没有唯一匹配的 API 监听进程')
    record = selected[0]
    process_identity(record['identity'])
    require(record.get('argv') == original['service']['command'], 'API 进程环境快照 argv 与原部署不一致')
    env = record.get('environment')
    require(isinstance(env, dict), 'API 进程缺少环境快照')
    return env


def paths(env):
    values = [env[key] for key in CANN_KEYS if key in env]
    for value in values:
        absolute(value, 'CANN 路径')
    return values


def validate(path, original, handoff, source=None):
    declared = paths(original['runtime'].get('env', {}))
    observed = paths(environment(source, original, handoff)) if source is not None else []
    if source is not None:
        require(observed, 'API 进程环境快照缺少 CANN 路径')
    require(declared or observed, '原部署环境及进程快照缺少 CANN 路径证据，不能从可读目录猜测')
    require(all(value == path for value in (*declared, *observed)),
            'cann_path 与 CANN 路径证据不一致或两种来源冲突')
    from pathlib import Path
    binding = sources.bound(source) if source else {}
    if 'path_checks' in binding or not Path(path).exists():
        checks = binding.get('path_checks', {})
        require(checks.get('identity') in handoff.get('workers', {}).get('listener_identities', []),
                '容器路径证据没有绑定实际 API 进程')
        require(checks.get('paths', {}).get(path, {}).get('is_dir') is True,
                'CANN 路径未在实际 API 进程命名空间中验证')
        model = handoff['model']['runtime_root']
        require(checks.get('paths', {}).get(model, {}).get('is_dir') is True,
                '部署模型路径未在实际 API 进程命名空间中验证')
