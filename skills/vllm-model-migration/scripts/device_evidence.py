"""设备映射及容量合同共用的静态文件身份；不依赖业务合同。"""
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ref(path):
    path = Path(path)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def bound(item):
    path = Path(item['path'])
    require(path.is_absolute() and path.resolve() == path, '设备证据路径无效')
    require(ref(path) == item, '设备证据摘要不一致')
    return json.loads(path.read_text())
