"""服务链路问题定界：接入 three-step-demarcation，对异常请求做三步定界。

diagnosis 模块负责「精度问题分析」里的问题定位；本文件是其中的「服务定界」——
给定一条异常请求（badcase）和服务端点，调 three-step-demarcation 出九类结论。
"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[4] / "scripts"))
from skill_paths import skill_root as release_skill  # noqa: E402

import json
from pathlib import Path
import hashlib
import subprocess
import sys
import uuid


def _demarcation_cli():
    """three-step-demarcation 的公开入口。"""
    return release_skill('three-step-demarcation') / 'scripts' / 'demarcation.py'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(8192), b''):
            digest.update(chunk)
    return digest.hexdigest()


def demarcate(service, badcase, work_root, run_id, timeout=120):
    """对一条异常请求做三步定界。

    service: {'endpoint', 'model'}；badcase: {'path', 'sha256'}（异常请求引用）。
    返回 (code, receipt)；receipt 为 three-step-demarcation 的 manifest（含 conclusions.demarcation）。
    """
    request = {'schema_version': '1.0', 'service': service, 'badcase': badcase,
               'render_mode': 'auto', 'timeout_seconds': timeout}
    req_dir = Path(work_root) / run_id / 'diagnosis'
    req_dir.mkdir(parents=True, exist_ok=True)
    req_path = req_dir / (uuid.uuid4().hex + '.request.json')
    req_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')

    args = [sys.executable, '-B', str(_demarcation_cli()),
            '--request', str(req_path), '--work-root', str(work_root), '--run-id', run_id]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, {'status': 'failed', 'diagnostic': {'type': 'TimeoutExpired', 'message': '定界超时'}}
    try:
        receipt = json.loads(proc.stdout) if proc.stdout.strip() else {}
        if not isinstance(receipt, dict):
            raise ValueError('定界未交付 JSON 对象')
        if receipt.get('report_path') and (receipt.get('inputs') != request
                or receipt.get('task_id') != 'three-step-demarcation' or receipt.get('run_id') != run_id
                or receipt.get('operation') != 'run'):
            raise ValueError('定界回执与委托身份不符')
    except ValueError as error:
        receipt = {'status': 'failed', 'diagnostic': {'type': 'InvalidReceipt', 'message': str(error)}}
    return proc.returncode, receipt


def demarcate_anomaly(anomaly, work_root, run_id, timeout=120):
    """对一条异常样本做三步定界；anomaly 含 request（内联）与 service。"""
    folder = Path(work_root) / run_id / 'diagnosis' / 'badcases'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex + '.json')
    path.write_text(json.dumps(anomaly['request'], ensure_ascii=False), encoding='utf-8')
    badcase = {'path': str(path), 'sha256': _sha256(path)}
    return demarcate(anomaly['service'], badcase, work_root, run_id, timeout)
