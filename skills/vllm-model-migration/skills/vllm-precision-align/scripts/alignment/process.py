"""自有公开CLI的等待与取消；服务生命周期留给原所有者。"""
import os
import signal
import subprocess

from .evidence import safe,write

GRACE_SECONDS=30


def terminate(process):
    process.send_signal(signal.SIGTERM)
    try:return (*process.communicate(timeout=GRACE_SECONDS),False)
    except subprocess.TimeoutExpired:
        if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
        else:process.kill()
        try:return (*process.communicate(timeout=5),True)
        except subprocess.TimeoutExpired:
            process.stdout.close();process.stderr.close()
            return '','自有CLI管道未关闭；资源交接需复核',True


def call(argv,timeout,attempt,label):
    process=None;failure=None;uncertain=False;stdout=stderr='';cancelled=False
    try:
        process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            cwd=attempt,start_new_session=os.name=='posix',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        try:stdout,stderr=process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired,KeyboardInterrupt) as error:
            failure=type(error).__name__;cancelled=isinstance(error,KeyboardInterrupt)
            previous={sig:signal.signal(sig,signal.SIG_IGN) for sig in (signal.SIGINT,signal.SIGTERM)}
            try:stdout,stderr,uncertain=terminate(process)
            finally:
                for sig,handler in previous.items():signal.signal(sig,handler)
    except OSError as error:failure=type(error).__name__;stderr=str(error)
    for suffix,text in (('stdout.log',stdout),('stderr.log',stderr)):
        try:(attempt/(label+'.'+suffix)).write_text(safe(text),encoding='utf-8')
        except OSError as error:
            from .evidence import EvidenceError
            raise EvidenceError('CLI日志无法保存') from error
    write(attempt/(label+'.process.json'),{'argv':argv,'cwd':str(attempt),'pid':process.pid if process else None,
        'returncode':process.returncode if process else None,'failure':failure,'resource_cleanup_unconfirmed':uncertain})
    if cancelled:raise KeyboardInterrupt('已通知自有CLI取消；服务资源以原所有者交接为准')
    if failure:raise RuntimeError(failure+'；查看自有CLI回执和资源状态，不宣称服务已清理')
    return process.returncode,stdout
