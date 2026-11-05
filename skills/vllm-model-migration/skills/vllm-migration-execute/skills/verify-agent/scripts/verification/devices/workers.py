"""全设备进程筛选和启动环境取证；不能只检查选中卡。"""
from pathlib import Path
import hashlib

from device_contract import SELECTORS, require


def verify_library(pid, expected):
    paths = set()
    for line in Path('/proc', str(pid), 'maps').read_text().splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) == 6 and Path(parts[5]).name.startswith('libascendcl.so'):
            paths.add(parts[5])
    require(paths == {expected['path']}, '实际 worker 加载的 CANN 库路径与映射探针不同')
    path = Path('/proc', str(pid), 'root') / expected['path'].lstrip('/')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    require(digest.hexdigest() == expected['sha256'], '实际 worker CANN 库内容变化')


def select_workers(observed, devices, same_owner, *, shared=False):
    owned = []
    for row in observed:
        try:
            owner = same_owner(row['pid'])
        except (FileNotFoundError, ProcessLookupError):
            if not shared:
                raise
            # 采样后进程已退出；仍由完整 worker 覆盖和服务身份签名约束自有进程。
            continue
        if owner:
            require(row['device'] in devices, '本服务在选中范围外使用物理卡')
            owned.append(row)
        elif row['device'] in devices and not shared:
            raise ValueError('所选物理卡存在其他任务的计算进程')
    require({row['device'] for row in owned} == set(devices), '所选物理卡缺少完整 worker 证据')
    return owned


def environment(pids, runtime, identity):
    """只读取声明的非凭据键及设备键；原始environ不写入证据。"""
    observed = []
    keys = set(runtime['env']) | set(SELECTORS)
    for pid in pids:
        before = identity(pid)
        data = Path('/proc', str(pid), 'environ').read_bytes()
        env = {}
        for part in data.split(b'\0'):
            key, sep, value = part.partition(b'=')
            name = key.decode(errors='replace')
            if sep and name in keys:
                env[name] = value.decode()
        require(before == identity(pid), '读取启动环境时 PID 身份变化')
        for key, value in runtime['env'].items():
            require(env.get(key) == value, '实际服务启动环境与声明不同：' + key)
        observed.append(env)
    require(bool(observed) and all(env == observed[0] for env in observed), 'API 启动环境不唯一')
    return {**runtime['env'], **{key: observed[0][key] for key in SELECTORS if key in observed[0]}}
