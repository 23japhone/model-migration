"""固定外部 JSON、请求副本和输出保护，不读取模型或执行配置。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat

from ..request import reference

ERRORS = (OSError, ValueError, TypeError, KeyError, RecursionError)
PATH_KEYS = {'path', 'root', 'runtime_root', 'context', 'output_root', 'protected_roots', 'weight_files', 'archive'}
REPORT_KEYS = {'source', 'deployment', 'dataset', 'preparation', 'result', 'report', 'config_index'}


class OutputError(ValueError):
    """输出侵入来源或资产，在创建尝试前拒绝。"""


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('JSON 重复字段')
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError('JSON 不允许非有限数字')


def parse(text):
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError('JSON 根必须是对象')
    json.dumps(value, ensure_ascii=False).encode('utf-8')
    return value


def stable_bytes(path, limit=None):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('来源必须为无符号链接的规范绝对路径')
    def opener(name, flags):
        return os.open(name, flags | getattr(os, 'O_NONBLOCK', 0))
    with open(path, 'rb', opener=opener) as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or (limit and before.st_size > limit):
            raise ValueError('来源须为大小受限的普通文件')
        raw = stream.read() if limit is None else stream.read(limit+1)
        after = os.fstat(stream.fileno())
    stamps = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in (before, after, path.stat())]
    if stamps[0] != stamps[1] or stamps[1] != stamps[2] or (limit and len(raw) > limit):
        raise ValueError('来源读取期间变化或超限')
    return raw


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(stable_bytes(path)).hexdigest()}


def read(path):
    return parse(stable_bytes(path, 64*1024*1024).decode('utf-8-sig'))


def bound(source):
    reference(source)
    raw = stable_bytes(source['path'], 64*1024*1024)
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('来源 SHA256 不一致')
    return parse(raw.decode('utf-8-sig'))


def prepare_request(value, base):
    """仅固定公开路径和默认值；业务校验仍由准备阶段完成。"""
    value = copy.deepcopy(value)
    value.setdefault('purpose', 'assets')
    value.setdefault('version_review', [])
    if not isinstance(value.get('tasks'), dict) or not isinstance(value['version_review'], list):
        raise ValueError('准备 tasks 须为对象，version_review 须为列表')
    forced = {'assets': (), 'precheck': ('model',), 'deployment': ('model','image'),
              'deployment_precision': ('model','image','dataset')}.get(value['purpose'], ())
    for key, item in value['tasks'].items():
        if not isinstance(item, dict):
            continue  # 条目错误由原阶段隔离，不阻止其他资产准备。
        path = item.get('request')
        if isinstance(path, str) and path.strip() and '\0' not in path:
            item['request'] = str((base/Path(path).expanduser()).resolve())
        item.setdefault('required', True)
        if key in forced and type(item['required']) is bool:
            item['required'] = True
        item.setdefault('timeout_seconds', 3600)
    for record in value['version_review']:
        if isinstance(record, dict) and isinstance(record.get('evidence_file'), str):
            record['evidence_file'] = str((base/Path(record['evidence_file']).expanduser()).resolve())
    return value


def paths(value, base):
    found = []
    if isinstance(value, list):
        for item in value:
            found.extend(paths(item, base))
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PATH_KEYS:
                for candidate in item if isinstance(item, list) else [item]:
                    if isinstance(candidate, str):
                        try:
                            found.append((base/Path(candidate).expanduser()).resolve())
                        except (OSError, ValueError, RuntimeError):
                            continue  # 无法规范化的声明不屏蔽其他可读资产。
            found.extend(paths(item, base))
    return found


def scan(value, base, protected, seen=None, depth=0):
    """收集可读来源边界；缺失业务依赖由阶段按各自契约处理。"""
    seen = set() if seen is None else seen
    protect_reports(value, protected)
    protected.extend(paths(value, base))
    if depth > 10:
        raise ValueError('来源链超过 10 层')
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    for item in items:
        if isinstance(item, dict) and set(item) == {'path', 'sha256'}:
            follow(item, base, protected, seen, depth)
        else:
            scan_nested(item, base, protected, seen, depth)


def follow(item, base, protected, seen, depth):
    source = item.get('path')
    if not isinstance(source, str) or source in seen:
        return
    seen.add(source)
    try:
        scan_document(bound(item), source, base, protected, seen, depth+1)
    except ERRORS:
        scan_untrusted(item, base, protected, seen, depth+1)


def scan_untrusted(source, base, protected, seen=None, depth=0):
    try:
        scan_document(read(source['path']), source['path'], base, protected, seen, depth)
    except ERRORS:
        return  # 仅保守保护可读声明；绝不将未绑定内容作为执行输入。


def scan_document(value, source, base, protected, seen, depth):
    if value.get('task_id') or 'format' in value or 'config_index' in value:
        protected.append(Path(source).parent)
    scan(value, base, protected, seen, depth)


def protect_reports(value, protected):
    if not isinstance(value, dict):
        return
    for key in REPORT_KEYS:
        item = value.get(key)
        if isinstance(item, dict) and isinstance(item.get('path'), str):
            protected.append(Path(item['path']).parent)


def scan_nested(item, base, protected, seen, depth):
    if isinstance(item, (dict, list)):
        try:
            scan(item, base, protected, seen, depth+1)
        except ERRORS:
            return  # 单个元数据分支不可读仍须扫描其他来源；有效性由原入口核验。


def protect(target, roots):
    if target.resolve() != target:
        raise OutputError('证据目录不能经符号链接重定向')
    for root in roots:
        root = Path(root).resolve()
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise OutputError('证据输出与来源或资产重叠')
