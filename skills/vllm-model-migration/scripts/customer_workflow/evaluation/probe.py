"""仅观察已绑定 venv；绝不安装、重建、降级或寻找替代环境。"""
from pathlib import Path
import stat
import subprocess
import uuid

from coordination.runtime import sources
from coordination.runtime.evidence import EvidenceError, write

CODE = '''import importlib.metadata as m,json,sys
try:
    version = m.version("evalscope")
except m.PackageNotFoundError:
    version = None
print(json.dumps({"python":sys.executable,"prefix":sys.prefix,"version":version}))
'''


class Changed(ValueError):
    """有实际证据确认绑定环境已变化。"""


def logs(folder, argv, result=None, error=None):
    for name in ('stdout', 'stderr'):
        value = getattr(result or error, name, '') or ''
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        write(folder / ('environment-probe.' + name + '.log'), value)
    write(folder / 'environment-probe.process.json', {
        'argv': argv, 'returncode': getattr(result, 'returncode', None), 'timeout_seconds': 30,
        'timed_out': isinstance(error, subprocess.TimeoutExpired), 'cancelled': isinstance(error, KeyboardInterrupt),
        'error': type(error).__name__ + ': ' + str(error) if error else None})


def inspect(session, binding, venv):
    folder = session.attempt / 'environment-probes' / uuid.uuid4().hex
    try:
        folder.mkdir(parents=True)
    except OSError as error:
        raise EvidenceError('只读环境探测证据目录不可写') from error
    argv = [binding['python'], '-B', '-c', CODE]
    try:
        if Path(venv).resolve() != Path(venv):
            raise Changed('绑定 venv 路径被重定向')
        mode = Path(binding['python']).stat().st_mode
    except FileNotFoundError as error:
        logs(folder, argv, error=error)
        raise Changed('绑定 Python 已消失') from error
    except (OSError, Changed) as error:
        logs(folder, argv, error=error)
        raise
    if not stat.S_ISREG(mode):
        logs(folder, argv, error=ValueError('绑定 Python 不再是普通文件'))
        raise Changed('绑定 Python 不再是普通文件')
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=30, cwd=folder)
    except subprocess.TimeoutExpired as error:
        logs(folder, argv, error=error)
        raise ValueError('绑定环境只读探测超过 30 秒，未确认环境变化') from error
    except (OSError, KeyboardInterrupt) as error:
        logs(folder, argv, error=error)
        raise
    logs(folder, argv, result=result)
    if result.returncode:
        raise ValueError('无法核验绑定环境，详见只读探测证据')
    value = sources.parse(result.stdout)
    return value


def check(session, options, binding):
    value = inspect(session, binding, options['venv'])
    if (set(value) != {'python', 'prefix', 'version'}
            or not all(isinstance(value.get(key), str) and value[key] for key in ('python', 'prefix'))
            or value['version'] is not None and (not isinstance(value['version'], str) or not value['version'].strip())):
        raise ValueError('只读环境探测结果不完整；未确认环境变化')
    if (value['python'] != binding['python'] or value['prefix'] != options['venv']
            or value['version'] != binding['version']):
        raise Changed('实际 Python、venv 归属或 EvalScope 版本已变化')
