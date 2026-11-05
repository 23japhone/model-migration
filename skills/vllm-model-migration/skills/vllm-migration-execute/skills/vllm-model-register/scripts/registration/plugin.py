"""生成标准 wheel 并仅安装到显式、由本任务管理的隔离 site。"""
import base64
import csv
import io
import json
from pathlib import Path
import zipfile

from .evidence import digest, read_bytes, read_json, write_json
from .process import run_command


def callback_source(request):
    template = Path(__file__).resolve().parents[2] / 'assets/plugin_callback.py.template'
    text = read_bytes(template).decode('utf-8')
    substitutions = {'@@ARCHITECTURE@@': request['model']['architecture'],
                     '@@MODULE@@': request['implementation']['module'],
                     '@@CLASS_NAME@@': request['implementation']['class_name']}
    for marker, value in substitutions.items():
        text = text.replace(marker, repr(value))
    return text.encode('utf-8')


def wheel_files(request):
    plugin = request['plugin']
    name, version = plugin['name'], plugin['version']
    metadata = name + '-' + version + '.dist-info'
    files = {
        name + '/__init__.py': callback_source(request),
        metadata + '/METADATA': (f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
            f"Requires-Python: >=3.10\nRequires-Dist: vllm=={request['target']['vllm']}\n").encode(),
        metadata + '/WHEEL': b'Wheel-Version: 1.0\nGenerator: vllm-model-register\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        metadata + '/entry_points.txt': (f"[vllm.general_plugins]\n{plugin['entrypoint']} = {name}:register\n").encode(),
    }
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    for path, data in sorted(files.items()):
        sha = base64.urlsafe_b64encode(bytes.fromhex(digest(data))).rstrip(b'=').decode()
        writer.writerow([path, 'sha256=' + sha, len(data)])
    writer.writerow([metadata + '/RECORD', '', ''])
    files[metadata + '/RECORD'] = output.getvalue().encode()
    return files


def project_identity(request):
    plugin = request['plugin']
    return {'schema_version': '1.0', 'owner': 'vllm-model-register', 'name': plugin['name'],
            'version': plugin['version'], 'entrypoint': plugin['entrypoint'],
            'architecture': request['model']['architecture'],
            'implementation': request['implementation'], 'callback_sha256': digest(callback_source(request))}


def verify_managed(path, marker, expected):
    if path.is_symlink():
        raise ValueError('plugin 输出目录不能是符号链接')
    if not path.exists():
        return False
    try:
        record, _ = read_json(path / marker)
    except OSError as error:
        raise ValueError('既有 plugin 目录不受本任务管理，不能覆盖') from error
    if record != expected:
        raise ValueError('既有 plugin 目录身份冲突，不允许覆盖')
    return True


def create_project(request, attempt):
    plugin = request['plugin']
    root = Path(plugin['project_root'])
    identity = project_identity(request)
    wheel = root / (plugin['name'] + '-' + plugin['version'] + '-py3-none-any.whl')
    files = wheel_files(request)
    if verify_managed(root, 'registration-project.json', identity):
        try:
            with zipfile.ZipFile(wheel) as archive:
                if set(archive.namelist()) != set(files) or any(archive.read(k) != v for k, v in files.items()):
                    raise ValueError('既有 wheel 内容已变化')
        except zipfile.BadZipFile as error:
            raise ValueError('既有 wheel 文件已损坏，不允许覆盖') from error
        if read_bytes(root / plugin['name'] / '__init__.py') != callback_source(request):
            raise ValueError('既有 plugin 源码已变化')
        return wheel
    root.mkdir(parents=True, exist_ok=False)
    (root / plugin['name']).mkdir()
    (root / plugin['name'] / '__init__.py').write_bytes(callback_source(request))
    with zipfile.ZipFile(wheel, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    write_json(root / 'registration-project.json', identity)
    write_json(attempt / 'plugin-artifact.json', {'identity': identity, 'wheel': str(wheel),
                                                'sha256': digest(wheel.read_bytes())})
    return wheel


def install_plugin(request, attempt):
    plugin = request['plugin']
    site = Path(plugin['install_site'])
    identity = project_identity(request)
    reused = verify_managed(site, '.vllm-model-register.json', identity)
    if reused:
        if read_bytes(site / plugin['name'] / '__init__.py') != callback_source(request):
            raise ValueError('已安装 plugin 内容与本次产物不一致')
        return {'action': 'reused', 'site': str(site), 'callback_sha256': identity['callback_sha256']}
    wheel = create_project(request, attempt)
    try:
        site.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ValueError('隔离 install_site 已被其他操作创建，不能覆盖') from error
    write_json(site / '.vllm-model-register.installing.json', identity)
    command = [request['runtime']['python'], '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
               'install', '--no-index', '--no-deps', '--no-compile', '--target', str(site), str(wheel)]
    code = run_command(command, request, attempt, 'plugin-install')
    if code != 0:
        raise ValueError('隔离 plugin 安装失败，不能以生成 wheel 代替初检')
    write_json(site / '.vllm-model-register.json', identity)
    return {'action': 'plugin_installed', 'site': str(site), 'wheel': str(wheel),
            'wheel_sha256': digest(wheel.read_bytes()), 'callback_sha256': identity['callback_sha256']}
