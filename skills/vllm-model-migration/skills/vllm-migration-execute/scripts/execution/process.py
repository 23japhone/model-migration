"""等待自有公开 CLI；取消先交由原生命周期所有者收敛。"""
import os
import signal
import subprocess

from .evidence import safe,write

GRACE_SECONDS = 30


def terminate(process):
    process.send_signal(signal.SIGTERM)
    try:return (*process.communicate(timeout=GRACE_SECONDS),False)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':os.killpg(process.pid,signal.SIGKILL)
        else:process.kill()
        try:return (*process.communicate(timeout=5),True)
        except subprocess.TimeoutExpired:
            process.stdout.close();process.stderr.close()
            return '', '自有CLI输出管道未收回；外部资源状态需复核',True


def call(argv,timeout,attempt,label):
    process = subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                               start_new_session=os.name=='posix',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    failure,uncertain = None,False
    try:stdout,stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired,KeyboardInterrupt) as error:
        failure = type(error).__name__
        previous = {s:signal.signal(s,signal.SIG_IGN) for s in [signal.SIGINT,signal.SIGTERM]}
        try:stdout,stderr,uncertain = terminate(process)
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
    (attempt/(label+'.stdout.log')).write_text(safe(stdout),encoding='utf-8')
    (attempt/(label+'.stderr.log')).write_text(safe(stderr),encoding='utf-8')
    write(attempt/(label+'.process.json'),{'argv':argv,'pid':process.pid,'returncode':process.returncode,
          'failure':failure,'resource_cleanup_unconfirmed':uncertain})
    if failure:raise RuntimeError(failure+'：已通知自有CLI；需读取原所有者的资源交接，未宣称清理服务')
    return process.returncode,stdout
