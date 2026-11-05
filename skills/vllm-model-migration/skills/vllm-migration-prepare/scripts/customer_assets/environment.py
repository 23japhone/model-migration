"""在任务专属 venv 准备固定版本，不修改系统或其他任务环境。"""
import hashlib
import json
from pathlib import Path
import re
import sys
import subprocess
from urllib.request import urlopen

from migration_prepare.process import call
from migration_prepare.results import safe_text, write_json

PACKAGES = {'ais-bench-benchmark', 'huggingface-hub', 'modelscope', 'modelscope-hub',
            'transformers', 'datasets', 'packaging'}


def selection(value, parent, attempt):
    if 'versions' in value:
        return value['versions']
    packages = value['packages']
    if not isinstance(packages, list) or not packages or any(name not in PACKAGES for name in packages):
        raise ValueError('隔离环境软件包列表无效')
    key = hashlib.sha256(json.dumps(sorted(set(packages))).encode()).hexdigest()
    frozen = parent / ('environment-selection-' + key + '.json')
    if frozen.exists():
        if frozen.is_symlink():
            raise ValueError('冻结版本文件不能是链接')
        return json.loads(frozen.read_text())
    versions = resolve_versions(packages, attempt)
    with frozen.open('x') as stream:
        json.dump(versions, stream)
    return versions


def stable_version(label):
    match = re.fullmatch(r'(\d+(?:\.\d+)*)(?:\.post(\d+))?', label)
    if not match:
        return None
    parts = tuple(int(part) for part in match[1].split('.'))
    return parts + (0,) * max(0, 4 - len(parts)) + (int(match[2] or 0),)


def resolve_versions(packages, attempt):
    if not isinstance(packages, list) or not packages or set(packages) - PACKAGES:
        raise ValueError('隔离环境只能安装已声明的评测或资产 SDK')
    versions = {}
    for name in sorted(set(packages)):
        url = 'https://pypi.org/pypi/' + name + '/json'
        with urlopen(url, timeout=30) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('官方发布索引过大')
        value = json.loads(raw)
        if value['info']['name'].lower().replace('_', '-') != name:
            raise ValueError('官方发布索引包身份不一致')
        releases = [(stable_version(label), label) for label, files in value['releases'].items()
                    if stable_version(label) is not None and any(not row.get('yanked', False) for row in files)]
        if not releases:
            raise ValueError('未找到可用稳定版本')
        versions[name] = max(releases)[1]
        (attempt / (name + '-index.json')).write_bytes(raw)
    write_json(attempt / 'versions.json', versions)
    return versions


def process_evidence(argv, result, attempt, label, **details):
    for name in ('stdout', 'stderr'):
        value = getattr(result, name, '') or ''
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        (attempt / (label + '.' + name + '.log')).write_text(safe_text(value))
    write_json(attempt / (label + '.process.json'),
               {'argv': argv, 'returncode': getattr(result, 'returncode', None), **details})


def logged_call(argv, attempt, label, timeout):
    try:
        result = call(argv, timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        process_evidence(argv, error, attempt, label, timed_out=isinstance(error, subprocess.TimeoutExpired),
                         cancelled=isinstance(error, KeyboardInterrupt), pid=getattr(error, 'pid', None))
        raise
    process_evidence(argv, result, attempt, label)
    return result


def command(argv, attempt, label, timeout=1800):
    result = logged_call(argv, attempt, label, timeout)
    if result.returncode:
        raise RuntimeError('隔离环境命令失败，详见准备证据')
    return result.stdout


def inspect(python, packages, attempt):
    code = ('import importlib.metadata as m,json,sys; '
            'print(json.dumps({"prefix":sys.prefix,"versions":{n:m.version(n) for n in ' + repr(list(packages)) + '}}))')
    return json.loads(command([str(python), '-B', '-c', code], attempt, 'environment-inspect', 60))


def install(python, root, versions, attempt, settings):
    command([settings.get('base_python', sys.executable), '-m', 'venv', str(root)], attempt, 'venv-create', 120)
    argv = [str(python), '-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple',
            '--disable-pip-version-check']
    if settings.get('constraints'):
        path = attempt / 'constraints.txt'
        path.write_text('\n'.join(settings['constraints']) + '\n')
        argv.extend(['--constraint', str(path)])
    command([*argv, *[name + '==' + value for name, value in sorted(versions.items())]], attempt, 'pip-install')


def prepare(versions, parent, attempt, **settings):
    if not versions or set(versions) - PACKAGES or any(stable_version(v) is None for v in versions.values()):
        raise ValueError('隔离环境必须使用已经冻结的明确稳定版本')
    fingerprint = hashlib.sha256(json.dumps([versions, settings] if settings else versions, sort_keys=True).encode()).hexdigest()
    root = parent / 'environments' / fingerprint
    python = root / 'bin/python'
    receipt = root / 'customer-environment.json'
    if root.exists():
        revalidate(root, python, receipt, versions, attempt)
        return {'python': str(python), 'versions': versions, 'operation': 'reused', 'receipt': str(receipt)}
    root.parent.mkdir(parents=True, exist_ok=True)
    install(python, root, versions, attempt, settings)
    actual = inspect(python, versions, attempt)
    if actual['versions'] != versions or Path(actual['prefix']).absolute() != root:
        raise ValueError('安装后的隔离环境版本或解释器身份不一致')
    frozen = command([str(python), '-m', 'pip', '--isolated', 'freeze', '--all'], attempt, 'pip-freeze', 60)
    write_json(receipt, {'versions': versions, 'python': str(python), 'resolved_dependencies': frozen.splitlines()})
    return {'python': str(python), 'versions': versions, 'operation': 'created', 'receipt': str(receipt)}


def revalidate(root, python, receipt, versions, attempt):
    if not receipt.is_file():
        raise ValueError('已有环境不完整或不属于此任务，不覆盖或升级')
    frozen = json.loads(receipt.read_text())
    if frozen.get('versions') != versions:
        raise ValueError('已有环境版本声明不同')
    actual = inspect(python, versions, attempt)
    if actual['versions'] != versions or Path(actual['prefix']).absolute() != root:
        raise ValueError('已有环境版本或路径已变化')
    dependencies = command([str(python), '-m', 'pip', '--isolated', 'freeze', '--all'], attempt, 'pip-freeze-check', 60)
    if sorted(dependencies.splitlines()) != sorted(frozen['resolved_dependencies']):
        raise ValueError('隔离环境的依赖版本已变化，不能复用原评测环境')
