"""有限评测进程；取消只作用于本次创建的子进程组。"""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess

from ..evidence import write


def stop(child):
    try:os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:child.wait(timeout=10)
    except subprocess.TimeoutExpired:pass
    finally:
        # 首进程退出不代表进程组已空；只清理 start_new_session 创建的自有组。
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        child.wait(timeout=10)


def execute(argv,folder,timeout,check=True):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    result={'argv':argv,'cwd':str(folder),'returncode':None}
    child=None
    try:
        with (folder/'stdout.log').open('wb') as stdout,(folder/'stderr.log').open('wb') as stderr:
            child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,
                                   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},cwd=folder)
            result['pid']=child.pid
            result['returncode']=child.wait(timeout=timeout)
        if check and result['returncode']!=0:raise RuntimeError('评测环境或配置探测失败，详见子进程日志')
    except BaseException as error:
        result['error']={'type':type(error).__name__,'message':str(error)}
        if child is not None:
            stop(child);result['returncode']=child.poll()
        if isinstance(error,subprocess.TimeoutExpired):raise TimeoutError('评测子进程超时') from error
        raise
    finally:
        write(folder/'process.json',result)
    return result


@contextmanager
def handling_signals():
    def cancelled(number,frame):
        raise KeyboardInterrupt('收到取消信号 '+str(number))
    previous={number:signal.signal(number,cancelled) for number in (signal.SIGINT,signal.SIGTERM)}
    try:yield
    finally:
        for number,handler in previous.items():signal.signal(number,handler)
