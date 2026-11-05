"""只终止本次拥有的 CLI 进程，回收部分输出。"""
import os
import signal
import subprocess


def stop(process):
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    return process.communicate()


def call(argv, timeout):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=os.name == 'posix',
                               env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        error.stdout, error.stderr = stop(process)
        error.pid = process.pid
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
