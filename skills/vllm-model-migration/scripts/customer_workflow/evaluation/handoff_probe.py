"""使用拟交接进程环境核验 Python 与 CLI；不运行安装、服务或性能负载。"""
import os
from pathlib import Path
import shutil
import stat
import subprocess

from coordination.runtime import sources
from coordination.runtime.evidence import write
from install_evalscope import REQUIRED_PERF_OPTIONS
from .probe import Changed

CODE = '''import importlib.metadata as m,json,sys,sysconfig
try:
    version = m.version("evalscope")
except m.PackageNotFoundError:
    version = None
print(json.dumps({"python":sys.executable,"prefix":sys.prefix,"version":version,
                  "scripts":sysconfig.get_path("scripts")}))
'''


def log(folder, label, argv, result=None, error=None):
    for stream in ('stdout', 'stderr'):
        content = getattr(result or error, stream, '') or ''
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        write(folder / (label + '.' + stream + '.log'), content)
    write(folder / (label + '.process.json'), {
        'argv': argv, 'returncode': getattr(result, 'returncode', None), 'timeout_seconds': 30,
        'timed_out': isinstance(error, subprocess.TimeoutExpired), 'cancelled': isinstance(error, KeyboardInterrupt),
        'error': type(error).__name__ + ': ' + str(error) if error else None})


def call(folder, label, argv, env):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=30, cwd=folder, env=env)
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        log(folder, label, argv, error=error)
        raise
    log(folder, label, argv, result=result)
    if result.returncode:
        raise ValueError(label + ' 探测退出非零，未确认身份变化')
    return result.stdout


def files(expected):
    venv = Path(expected['venv'])
    if venv.resolve() != venv:
        raise Changed('绑定 venv 路径被重定向')
    for key in ('benchmark_python', 'evalscope_executable'):
        path = Path(expected[key])
        try:
            mode = path.stat().st_mode
        except FileNotFoundError as error:
            raise Changed('绑定文件已消失：' + key) from error
        if not stat.S_ISREG(mode):
            raise Changed('绑定文件不再是普通文件：' + key)
    # Python 的系统解释器链接须保留启动路径；CLI 不得被转向另一环境。
    cli = Path(expected['evalscope_executable'])
    if cli.resolve().parent != cli.parent:
        raise Changed('绑定 EvalScope CLI 被重定向到其他目录')


def python_identity(text, expected):
    observed = sources.parse(text)
    fields = ('python', 'prefix', 'scripts')
    if set(observed) != {*fields, 'version'}:
        raise ValueError('Python 观察字段不完整，无法核验')
    if not all(isinstance(observed[key], str) and observed[key].strip() for key in fields):
        raise ValueError('Python 观察路径无效，无法核验')
    version = observed['version']
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ValueError('EvalScope 观察版本无效，无法核验')
    bound = {'python': expected['benchmark_python'], 'prefix': expected['venv'],
             'version': expected['evalscope_version'], 'scripts': str(Path(expected['evalscope_executable']).parent)}
    if observed != bound:
        raise Changed('实际 Python、venv、scripts 目录或 EvalScope 版本已变化')


def check(expected, folder):
    directory = str(Path(expected['evalscope_executable']).parent)
    path = os.pathsep.join(filter(None, (directory, os.environ.get('PATH', ''))))
    env = {**os.environ, 'PATH': path, 'PYTHONDONTWRITEBYTECODE': '1'}
    write(folder / 'proposed-environment.json', {'PATH': path, 'PYTHONDONTWRITEBYTECODE': '1'})
    files(expected)
    python_identity(call(folder, 'python', [expected['benchmark_python'], '-B', '-c', CODE], env), expected)
    resolved = shutil.which('evalscope', path=path)
    write(folder / 'cli-resolution.json', {'PATH': path, 'resolved': resolved})
    if resolved != expected['evalscope_executable']:
        raise ValueError('拟交接 PATH 无法解析到绑定 CLI，禁止性能启动')
    help_text = call(folder, 'perf-help', [resolved, 'perf', '--help'], env)
    if any(option not in help_text for option in REQUIRED_PERF_OPTIONS):
        raise ValueError('perf --help 缺少必需选项，无法核验兼容性')
    return {**expected, 'env': {'PATH': path, 'PYTHONDONTWRITEBYTECODE': '1'}}
