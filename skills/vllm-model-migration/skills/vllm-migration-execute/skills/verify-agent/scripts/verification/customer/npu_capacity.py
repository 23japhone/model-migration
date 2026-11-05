"""本次容量观察与分配证据绑定；独立离线 allocate 保留显式数组接口。"""
import math
from pathlib import Path
import socket
import time

from device_contract import require
from ..devices.capacity_table import parse
from ..evidence import read_bound, sha256
from ..runtime.command import run
from .capacity import select

MAX_AGE_SECONDS = 60


def host_identity():
    return {'hostname': socket.gethostname(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def observe(attempt):
    identity = host_identity()
    started = time.time()
    prefix = attempt / 'npu-observation'
    raw = run(['npu-smi', 'info'], prefix, timeout=30)
    require(identity == host_identity(), '容量观察期间主机启动身份变化')
    path = Path(str(prefix) + '.stdout.log')
    return {'devices': parse(raw), 'observed_at': started, 'host_identity': identity,
            'npu_evidence': {'path': str(path), 'sha256': sha256(path)},
            'npu_raw': raw, 'npu_source': str(path)}


def bound_devices(reference, *, fresh=True):
    report = read_bound(reference)
    require(isinstance(report, dict) and report.get('status') == 'completed'
            and report.get('operation') == 'observe', '分配缺少 completed 主机观察')
    observed = report.get('result', {})
    require(isinstance(observed, dict), '容量观察结果不是对象')
    require(observed.get('host_identity') == host_identity(), '容量观察不是当前主机/启动身份')
    stamp = observed.get('observed_at')
    require(type(stamp) in (int, float) and math.isfinite(stamp), '容量观察缺少采集时间')
    age = time.time() - stamp
    require(age >= 0 and (not fresh or age <= MAX_AGE_SECONDS), '容量观察过期或来自未来，请重新采集后分配')
    source = observed.get('npu_evidence', {})
    require(isinstance(source, dict) and set(source) == {'path', 'sha256'}, '容量观察缺少原始证据引用')
    require(sha256(source['path']) == source['sha256'], '容量原始证据摘要变化')
    raw = Path(source['path']).read_text(encoding='utf-8')
    require(raw == observed.get('npu_raw'), '容量观察与原始输出不一致')
    devices = parse(raw)
    require(devices == observed.get('devices'), '容量设备记录与原始输出不一致')
    return {row['id']: row for row in devices}


def allocate(request):
    if request.get('policy') is not None:
        require('observation' in request, '新分配策略必须绑定本次容量观察')
    if 'observation' in request:
        devices = bound_devices(request['observation'])
        for row in request['devices']:
            require(devices.get(row['id']) == row, '分配设备状态未使用绑定的本次观察')
    return select(request['spec'], request['devices'], request['requested'], policy=request.get('policy'))
