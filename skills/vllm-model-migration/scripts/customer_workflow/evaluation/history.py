"""恢复可证明的安装选择；不从评测 Python 路径猜基础解释器。"""
import os
from pathlib import Path

from coordination.runtime import sources
from evalscope_installation import STRINGS, validate


class Unavailable(ValueError):
    """历史选择不足以安全重放；需要新任务，不能回退到全局默认。"""


def runtime_python(venv):
    return str(Path(venv) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))


def chosen(request):
    if request.get('operation') != 'evalscope_environment':
        raise Unavailable('旧环境选择回执要求新 run-id')
    options = validate(request.get('install', {}))
    for name in ('python', 'venv'):
        value = options.get(name)
        if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
            raise Unavailable('无法确认原基础 Python 或 venv，要求新 run-id')
    return {name: options[name] for name in STRINGS if name in options}


def binding(value, options):
    old = value.get('evalscope_environment')
    if old is None:
        return None
    if (not isinstance(old, dict) or old.get('python') != runtime_python(options['venv'])
            or not isinstance(old.get('version'), str) or not old['version'].strip()
            or 'version' in options and old['version'] != options['version']):
        raise Unavailable('安装选择与实际环境绑定不一致，要求新 run-id')
    return old


def recorded(value):
    record = value['evalscope_install_selection']
    if not isinstance(record, dict) or set(record) != {'options', 'source'}:
        raise Unavailable('安装选择记录不完整，要求新 run-id')
    options = chosen(sources.bound(record['source']))
    if record['options'] != options:
        raise Unavailable('安装选择与请求证据不一致，要求新 run-id')
    binding(value, options)
    return record


def recovered(value, row):
    if not row.get('request') or not row.get('receipt'):
        raise Unavailable('缺少可靠安装请求/成功回执，要求新 run-id')
    request = sources.bound(row['request'])
    receipt = sources.bound(row['receipt'])
    result = receipt.get('result', {})
    options = chosen(request)
    old = binding(value, options)
    if (receipt.get('status') != 'completed' or receipt.get('operation') != 'evalscope_environment'
            or receipt.get('inputs') != request or result.get('method') != 'install_evalscope'):
        raise Unavailable('旧环境回执不能可靠恢复安装选择，要求新 run-id')
    check_result(result, options, old)
    return {'options': options, 'source': row['request']}


def check_result(result, options, old):
    if (old is None or result.get('status') != 'success' or result.get('venv') != options['venv']
            or result.get('python') != old['python'] or result.get('evalscope_version') != old['version']
            or result.get('versions', {}).get('evalscope') != old['version']):
        raise Unavailable('成功安装结果与环境绑定不一致，要求新 run-id')
    package = 'evalscope[perf]' + ('==' + options['version'] if 'version' in options else '')
    if result.get('package') != package:
        raise Unavailable('成功安装结果与版本要求不一致，要求新 run-id')


def selected(value):
    if 'evalscope_install_selection' in value:
        return recorded(value)
    row = value.get('steps', {}).get('environment', {})
    if not row and 'evalscope_environment' not in value:
        return None
    if row.get('status') == 'not_run' and 'evalscope_environment' not in value:
        return None  # 旧帮助/预览不建立环境选择。
    return recovered(value, row)
