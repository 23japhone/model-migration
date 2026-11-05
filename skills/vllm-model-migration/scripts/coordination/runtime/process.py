"""仅收敛自有阶段 CLI；重复取消及遗留管道不能使等待无限延长。"""
import os
import signal
import subprocess
import time

from .evidence import defer_interrupts, write


def decoded(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''


def kill_cli(process):
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass  # CLI 已在检查与发信号之间退出；不寻找或停止其他服务。


def final_drain(process):
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired as error:
        # 派生进程可能仍持有管道；不能让 communicate 无界等待。
        for name in ('stdout', 'stderr'):
            stream = getattr(process, name, None)
            if stream is not None:
                stream.close()
        process.poll()
        return decoded(error.output), decoded(error.stderr)


def converge(process, received=None):
    received = [] if received is None else received
    with defer_interrupts(received):
        if process.poll() is not None:
            return final_drain(process), False
        try:
            process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return final_drain(process), False
        try:
            return process.communicate(timeout=30), False
        except subprocess.TimeoutExpired:
            kill_cli(process)
            return final_drain(process), True


def record(attempt, label, process, argv, started, output, stopped, forced, received):
    with defer_interrupts(received):
        write(attempt/(label+'.stdout.txt'), output[0])
        write(attempt/(label+'.stderr.txt'), output[1])
        value = {'argv': argv, 'cwd': str(attempt), 'pid': process.pid,
                 'returncode': process.returncode, 'elapsed_seconds': time.time()-started,
                 'interrupted': type(stopped).__name__ if stopped else None,
                 'forced_cli_reap': forced, 'resource_cleanup_unconfirmed': bool(stopped),
                 'deferred_signals': received}
        if received:
            value.update(interrupted='KeyboardInterrupt', resource_cleanup_unconfirmed=True)
        path = attempt/(label+'.process.json')
        write(path, value)
        if received and value['interrupted'] != 'KeyboardInterrupt':
            value.update(interrupted='KeyboardInterrupt', resource_cleanup_unconfirmed=True)
            write(path, value)


def call(argv, timeout, attempt, label):
    started = time.time()
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace', cwd=attempt,
                               start_new_session=os.name == 'posix')
    stopped, forced, received = None, False, []
    try:
        output = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        stopped = error
        output, forced = converge(process, received)
    record(attempt, label, process, argv, started, output, stopped, forced, received)
    if isinstance(stopped, KeyboardInterrupt) or received:
        raise KeyboardInterrupt('总入口取消；阶段清理结果需复核')
    if stopped:
        raise RuntimeError('阶段 CLI 超时；资源清理未确认，查看进程证据')
    return process.returncode, output[0]
