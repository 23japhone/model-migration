"""调用原版安装脚本并核对实际环境；不发现候选、不重试或切换环境。"""
import json
import os
from pathlib import Path
import sys
import shutil

from evalscope_installation import arguments, normalize, protect
from skill_paths import plugin_root
from . import environment


def trailing_json(output):
    # pip 继承 stdout；结果是最后一个完整 JSON，前面可以有普通日志。
    decoder = json.JSONDecoder()
    for index in range(len(output) - 1, -1, -1):
        if output[index] != '{':
            continue
        try:
            value, end = decoder.raw_decode(output, index)
        except ValueError:
            continue
        if isinstance(value, dict) and not output[end:].strip():
            return value
    raise ValueError('安装脚本 stdout 末尾缺少完整 JSON 结果；详见安装日志')


def check_paths(result, options):
    root = Path(options['venv'])
    relative = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    if result.get('venv') != str(root) or result.get('python') != str(root / relative):
        raise ValueError('安装返回的 venv 或 Python 与本次配置不一致')
    return (root / relative).parent


def ready(result, options):
    scripts = check_paths(result, options)
    if result.get('status') != 'success':
        raise ValueError('安装脚本未返回 success；环境未就绪')
    if result.get('evalscope_executable') not in [str(scripts / name) for name in ('evalscope', 'evalscope.exe')]:
        raise ValueError('安装返回的 CLI 不属于本次 venv')
    version = result.get('evalscope_version')
    if not isinstance(version, str) or not version.strip():
        raise ValueError('安装返回的 EvalScope 版本无效')
    if 'version' in options and version != options['version']:
        raise ValueError('安装返回的实际 EvalScope 版本与指定版本不一致')
    package = 'evalscope[perf]' + ('==' + options['version'] if 'version' in options else '')
    if result.get('package') != package:
        raise ValueError('安装返回的软件包与请求不一致')
    return {**result, 'method': 'install_evalscope', 'versions': {'evalscope': version},
            'performance_handoff': performance_handoff(result)}


def performance_handoff(result):
    directory = str(Path(result['evalscope_executable']).parent)
    path = directory + os.pathsep + os.environ.get('PATH', '')
    resolved = shutil.which('evalscope', path=path)
    if resolved != result['evalscope_executable']:
        raise ValueError('PATH 实际解析的 EvalScope CLI 与安装结果不一致')
    return {'benchmark_python': result['python'], 'venv': result['venv'], 'env': {'PATH': path},
            'evalscope_executable': resolved, 'evalscope_version': result['evalscope_version']}


def install(value, attempt):
    options = normalize(value.get('install', {}))
    protect(options, [attempt])
    argv = [sys.executable, '-B', str(plugin_root() / 'scripts/install_evalscope.py'), *arguments(options)]
    run = environment.logged_call(argv, attempt, 'evalscope-install', 1800)
    if run.returncode:
        raise ValueError('EvalScope 安装脚本退出失败，详见 stdout、stderr 和 process 回执；不自动重试')
    if options.get('help'):
        return {'status': 'not_run', 'mode': 'help', 'help': run.stdout}
    result = trailing_json(run.stdout)
    if options.get('dry_run'):
        check_paths(result, options)
        if result.get('status') != 'planned':
            raise ValueError('安装预览未返回 planned')
        return {'status': 'not_run', 'mode': 'dry_run', 'plan': result}
    return ready(result, options)
