"""目标短任务的执行与取消协议；Docker客户端退出不代替内部任务结束。"""
from pathlib import Path
import uuid

from . import command
from device_contract import SELECTORS


def docker_prefix(resource, environment, flags=()):
    argv = ['docker', 'exec', *flags]
    for key, value in environment.items():
        argv.extend(['--env', key + '=' + value])
    return [*argv, resource['identity']['container_id']]


def prefix(request, resource):
    python = request['runtime']['python']
    start = []
    if request['runtime']['kind'] == 'docker':
        start = docker_prefix(resource, request['runtime']['env'], ('-i',))
    code = Path(__file__).with_name('job.py').read_text()
    absent = [key for key in SELECTORS if key not in request['runtime']['env']]
    code = 'import os\nfor key in ' + repr(absent) + ': os.environ.pop(key, None)\n' + code
    return [*start, python, '-B', '-c', code, 'run', uuid.uuid4().hex, python]


def execute(prefix, argv, log_base, **kwargs):
    job_id = prefix[-2]
    cancel_argv = [*prefix[:-3], 'cancel', job_id]
    log = Path(log_base)
    def cancel():
        command.run(cancel_argv, log.with_name(log.name + '.cancel'), timeout=10,
                    env=kwargs.get('env'), cwd=kwargs.get('cwd'))
    return command.run([*prefix, *argv], log, cancel=cancel, **kwargs)
