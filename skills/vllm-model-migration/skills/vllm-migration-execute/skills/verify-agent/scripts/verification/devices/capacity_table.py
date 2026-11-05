"""已知 910B info 表的 HBM 与完整进程表；未知或缺失字段不能当作空闲。"""
import re

from device_contract import require


def cells(raw):
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r'[+\-=]+', line):
            continue
        require(line.startswith('|') and line.endswith('|'), '未知 npu-smi 表格行')
        rows.append([part.strip() for part in line[1:-1].split('|')])
    return rows


def memory_row(row, device):
    require(len(row) == 3 and row[0] == '0', '容量观察仅支持 910B 整卡 Chip 0')
    require(re.fullmatch(r'[\da-fA-F]{4}:[\da-fA-F]{2}:[\da-fA-F]{2}\.\d', row[1]), '未知 Bus-Id 布局')
    match = re.fullmatch(r'\d+(?:\.\d+)?\s+\d+\s*/\s*\d+\s+(\d+)\s*/\s*(\d+)', row[2])
    require(match is not None, 'HBM 容量缺失或布局未知')
    used, total = map(int, match.groups())
    require(0 <= used <= total and total > 0, 'HBM 容量数值无效')
    return {'id': device, 'total_bytes': total * 1048576, 'free_bytes': (total - used) * 1048576}


def device_rows(rows):
    require(len(rows) % 2 == 0 and bool(rows), '设备容量行不完整')
    result = {}
    for index in range(0, len(rows), 2):
        row = rows[index]
        require(len(row) == 3, '未知 NPU 容量列')
        match = re.fullmatch(r'(\d+)\s+910B\w*', row[0])
        require(match is not None and row[1] == 'OK', '型号未知或 NPU 健康状态非 OK')
        device = int(match[1])
        require(device not in result, '重复 NPU 容量行')
        result[device] = memory_row(rows[index + 1], device)
    return result


def process_rows(rows, devices):
    occupied, empty = set(), set()
    for row in rows:
        clear = re.fullmatch(r'No running processes found in NPU (\d+)', row[0])
        if clear and len(row) == 1:
            device = int(clear[1])
            require(device not in empty, '重复空闲进程声明')
            empty.add(device)
        else:
            require(len(row) == 4, '未知 NPU 进程列')
            match = re.fullmatch(r'(\d+)\s+0', row[0])
            require(match is not None and row[1].isdecimal() and int(row[1]) > 0
                    and bool(row[2]) and row[3].isdecimal(), '无法解析 NPU 计算进程行')
            occupied.add(int(match[1]))
    require(not occupied & empty and occupied | empty == set(devices), '进程表缺卡、越界或占用状态冲突')
    return occupied


def parse(raw):
    rows = cells(raw)
    require(len(rows) >= 6 and len(rows[0]) == 1 and rows[0][0].startswith('npu-smi '), '未知 npu-smi 版本表头')
    require([re.sub(r'\s+', ' ', v) for v in rows[1]] == ['NPU Name', 'Health', 'Power(W) Temp(C) Hugepages-Usage(page)'],
            '未知 NPU 名称表头')
    require([re.sub(r'\s+', ' ', v) for v in rows[2]] ==
            ['Chip', 'Bus-Id', 'AICore(%) Memory-Usage(MB) HBM-Usage(MB)'], '未知 HBM 表头或单位')
    header = ['NPU Chip', 'Process id', 'Process name', 'Process memory(MB)']
    positions = [i for i, row in enumerate(rows) if [re.sub(r'\s+', ' ', v) for v in row] == header]
    require(len(positions) == 1, '缺少唯一的 NPU 进程表')
    split = positions[0]
    devices = device_rows(rows[3:split])
    occupied = process_rows(rows[split + 1:], devices)
    return [{**devices[n], 'occupied': n in occupied} for n in sorted(devices)]
