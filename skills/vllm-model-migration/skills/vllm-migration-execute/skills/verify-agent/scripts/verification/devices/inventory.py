"""910B 整卡 npu-smi 布局适配；保留 NPU/Chip 编号，不猜测未知布局。"""
import re

from device_contract import require
from ..runtime.command import run


def mapping(raw):
    require('NPU ID' in raw and 'Chip Logic ID' in raw, '不支持的 npu-smi 映射表布局')
    result = {}
    for line in raw.splitlines():
        if not re.match(r'^\s*\d+\s', line):
            continue
        parts = line.split(maxsplit=3)
        require(len(parts) == 4, 'npu-smi 映射行不完整')
        npu, chip, logical, name = parts
        name = name.strip()
        if logical == '-' and name.lower() == 'mcu':
            continue
        require(re.fullmatch(r'Ascend\s*910B\w*', name) and chip == '0' and logical.isdecimal(),
                '首版只支持 Ascend 910B 整卡映射')
        key = (int(npu), int(chip))
        require(key not in result, '重复 NPU/Chip 映射')
        result[key] = {'physical_id': int(npu), 'host_logical_id': int(logical)}
    require(bool(result), '未观察到可核对的 910B 整卡')
    return result


def processes(raw, inventory):
    require('Process id' in raw, '未知 npu-smi 进程布局')
    found = []
    for line in raw.split('Process id', 1)[1].splitlines()[1:]:
        if not re.match(r'^\|\s*\d', line):
            continue
        match = re.match(r'^\|\s*(\d+)\s+(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+)\|', line)
        require(match is not None, '无法解析 NPU 计算进程行')
        key = (int(match[1]), int(match[2]))
        require(key in inventory, '计算进程 NPU/Chip 不在已核对的物理映射中')
        found.append({'device': inventory[key]['physical_id'], 'npu_id': key[0], 'chip_id': key[1],
                      'pid': int(match[3]), 'name': match[4].strip()})
    return found


def snapshot(attempt, label):
    devices = mapping(run(['npu-smi', 'info', '-m'], attempt / (label + '-mapping'), timeout=15))
    raw = run(['npu-smi', 'info'], attempt / label, timeout=15)
    return processes(raw, devices)
