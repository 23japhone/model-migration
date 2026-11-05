"""短子进程的运行、超时与取消；只清理本次拥有的进程组。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from .evidence import read_json, safe_text, write_json


def environment(request, attempt):
    env = dict(os.environ)
    scripts = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = os.pathsep.join([scripts, *request['runtime']['import_roots']])
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['VLLM_PLUGINS'] = ','.join(request['runtime']['plugins'])
    for name, directory in {'VLLM_CACHE_ROOT': 'vllm', 'VLLM_CONFIG_ROOT': 'vllm-config',
                            'TORCHINDUCTOR_CACHE_DIR': 'torch', 'TRITON_CACHE_DIR': 'triton', 'HF_HOME': 'hf'}.items():
        env[name] = str(attempt / 'caches' / directory)
    env['HF_HUB_OFFLINE'] = '1'
    env['TRANSFORMERS_OFFLINE'] = '1'
    return env


def signal_owned_group(process, sig):
    status = process.poll()
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        group = process.pid
    else:
        if status is not None:
            raise RuntimeError('原进程 PID 已复用，不能清理身份变化的进程组')
    if group != process.pid:
        raise RuntimeError('短进程身份变化，不能清理未知进程组')
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        return False
    return True


def wait_owned_group(process, deadline):
    while signal_owned_group(process, 0):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(.05, remaining))
    return True


def stop_owned(process):
    deadline = time.monotonic() + 3
    signal_owned_group(process, signal.SIGTERM)
    try:
        output = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        output = None
    # 进程组仍存在时，即使主进程已退出，也完成自有派生进程清理。
    if not wait_owned_group(process, deadline):
        signal_owned_group(process, signal.SIGKILL)
    if output is not None:
        return output
    try:
        return process.communicate(timeout=3)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('拥有的进程组已终止，但输出管道仍未关闭；不清理脱离原组的未知进程') from error


def run_command(command, request, attempt, label):
    record_path = attempt / (label + '-process.json')
    record = {'command': command, 'context': request['runtime']['context'], 'status': 'running'}
    write_json(record_path, record)
    process = subprocess.Popen(command, cwd=attempt, env=environment(request, attempt),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    record['pid'] = process.pid
    try:
        write_json(record_path, record)
        process.communicate(timeout=request['runtime']['timeout_seconds'])
        record.update(status='exited', exit_code=process.returncode)
    except subprocess.TimeoutExpired:
        record['status'] = 'timeout'
    except KeyboardInterrupt:
        record['status'] = 'cancelled'
    finally:
        try:
            out, err = stop_owned(process)
        except RuntimeError as error:
            record.update(status='cleanup_failed', error=str(error), exit_code=process.returncode)
            write_json(record_path, record)
            raise
        record['exit_code'] = process.returncode
        save_logs(attempt, label, out, err)
        write_json(record_path, record)
    if record['status'] == 'timeout':
        raise RuntimeError('独立检查或安装超时：' + label)
    if record['status'] == 'cancelled':
        raise KeyboardInterrupt
    return process.returncode


def save_logs(attempt, label, out, err):
    (attempt / (label + '-stdout.log')).write_text(safe_text(out))
    (attempt / (label + '-stderr.log')).write_text(safe_text(err))


def run_probe(request, attempt, phase):
    nonce = uuid.uuid4().hex
    payload = attempt / (phase + '-probe-input.json')
    output = attempt / (phase + '-probe-output.json')
    write_json(payload, {'probe_version': '1.0', 'nonce': nonce, 'phase': phase, 'request': request})
    command = [request['runtime']['python'], '-B', '-m', 'registration.probe', str(payload), str(output)]
    code = run_command(command, request, attempt, phase)
    if not output.is_file():
        raise ValueError('目标独立进程未生成有效检查回执，见原始日志')
    result, _ = read_json(output)
    process_record, _ = read_json(attempt / (phase + '-process.json'))
    if result.get('probe_version') != '1.0' or result.get('nonce') != nonce or result.get('phase') != phase:
        raise ValueError('独立检查回执身份不一致')
    if result.get('pid') != process_record.get('pid') or result.get('context') != request['runtime']['context']:
        raise ValueError('独立检查 PID 或运行用途不一致')
    if code != 0 or result.get('ok') is not True:
        raise ValueError('独立检查未通过：' + '; '.join(result.get('errors', ['目标检查失败'])))
    return result
