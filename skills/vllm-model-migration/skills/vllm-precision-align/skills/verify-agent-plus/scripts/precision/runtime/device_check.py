"""通过 verify-agent 公开只读 CLI 复核 NPU；不导入兄弟 Skill 实现。"""
import json
from pathlib import Path
import sys

from device_contract import ref, require
from skill_paths import skill_root
from .process import execute
from ..evidence import bound, write


def inspect(inputs, attempt, label):
    raw = inputs['deployment_raw']
    handoff = {'path': raw['evidence']['handoff'], 'sha256': raw['detailed_report']['handoff_sha256']}
    require(bound(handoff) == inputs['handoff'], '部署交接在设备复核前发生变化')
    request = {'handoff': handoff}
    request_path = attempt / (label + '-devices.request.json')
    write(request_path, request)
    output = attempt / (label + '-devices')
    command = [sys.executable, '-B', str(skill_root('verify-agent') / 'scripts/verify.py'),
               '--action', 'inspect-devices', '--request', str(request_path),
               '--work-root', str(output), '--run-id', 'inspection']
    result = execute(command, attempt / (label + '-devices-process'), 180, check=False)
    text = (attempt / (label + '-devices-process') / 'stdout.log').read_text()
    require(bool(text.strip()), '设备复核没有公开回执')
    summary = json.loads(text.strip().splitlines()[-1])
    path = Path(summary['report_path'])
    require(path.resolve() == path and path.is_relative_to(output), '设备复核回执越界')
    report = bound(ref(path))
    require(report['inputs'] == request and report.get('operation') == 'inspect-devices' and
            report.get('task_id') == 'verify-agent', '设备复核回执来源不符')
    require(result['returncode'] == 0 and report['status'] == 'completed' and
            report['conclusions'].get('devices') == 'pass' and report.get('success') is True,
            '设备映射/实际用卡复核未通过，详见 ' + str(path))
    write(attempt / (label + '-device-inspection.json'), {'report': ref(path)})
    return identity_snapshot(report)


def identity_snapshot(report):
    details = report['detailed_report']
    evidence = details['device_evidence']
    mapping = bound(evidence['device_mapping'])
    workers = [{'device': row['device'], 'pid': row['pid'], 'identity': row['identity']}
               for row in details['workers']['npu_processes']]
    return {'npu_processes': sorted(workers, key=lambda row: (row['device'], row['pid'])),
            'device_mapping': {key: mapping[key] for key in ('host', 'resource', 'devices', 'library', 'executable_realpath')},
            'effective_runtime': bound(evidence['effective_runtime'])['runtime']}
