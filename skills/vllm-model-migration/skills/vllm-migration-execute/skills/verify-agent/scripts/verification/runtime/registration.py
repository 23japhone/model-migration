"""只调用vllm-model-register公开check CLI，校验本次最终载体回执。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[7] / "scripts"))
from skill_paths import registration_bundle, registration_files, resource_snapshot, verify_snapshot
import json
import os
from pathlib import Path
import shutil

from .command import run
from .execution import execute
from ..evidence import write_json


def copy_resources(root, files, destination):
    verify_snapshot(registration_files(root), files, root)
    destination.mkdir()
    for relative in files:
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root/relative, target)
    verify_snapshot(registration_files(root), files, root)
    verify_snapshot(registration_files(destination), files, destination)


def stage(request, resource, prefix, attempt):
    item = request['registration']
    root, check_entry = registration_bundle(item['resources'])
    files = resource_snapshot(registration_files(root), root)
    if check_entry not in files:raise ValueError('缺少注册公开检查入口')
    write_json(attempt / 'registration-resource-identity.json', files)
    request_path = Path(item['request'])
    checked = json.loads(request_path.read_text())
    if checked.get('model', {}).get('architecture') not in request['_architectures']:
        raise ValueError('注册检查架构与本次原始配置不一致')
    config = str(Path(request['model']['runtime_root']) / request['model']['config'])
    expected = request['runtime']['files']
    if checked['model']['config'] != config:
        raise ValueError('注册检查未指向本次运行载体的原始配置')
    for section, path_key, digest_key in [('implementation', 'path', 'sha256'),
                                         ('target', 'registry_path', 'registry_sha256')]:
        value = checked[section]
        if expected.get(value[path_key]) != value[digest_key]:
            raise ValueError('注册检查实现/registry与本次运行身份清单不一致')
    write_json(attempt / 'registration-check.request.json', checked)
    verify_snapshot(registration_files(root), files, root)
    destination = '/tmp/vllm-verify-' + resource['nonce']
    if resource['kind'] == 'docker':
        snapshot = attempt/'registration-resources'
        copy_resources(root, files, snapshot)
        execute(prefix, ['-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()', destination],
            attempt / 'registration-mkdir')
        cid = resource['identity']['container_id']
        run(['docker', 'cp', str(snapshot), cid + ':' + destination + '/resources'], attempt / 'registration-copy')
        run(['docker', 'cp', str(attempt / 'registration-check.request.json'), cid + ':' + destination + '/check.json'],
            attempt / 'registration-copy-request')
        return destination + '/resources/' + check_entry, destination + '/check.json', destination + '/evidence'
    return str(root / check_entry), str(request_path), str(attempt / 'registration-evidence')


def check(request, resource, prefix, attempt):
    script, config, output = stage(request, resource, prefix, attempt)
    raw = execute(prefix, ['-B', script, '--request', config, '--work-root', output, '--run-id', 'carrier'],
              attempt / 'registration-check', timeout=180,
              env={**os.environ, **request['runtime']['env']}, cwd=request['runtime'].get('cwd') if request['runtime']['kind'] == 'process' else None)
    result = json.loads(raw.strip().splitlines()[-1])
    path = result['manifest']
    if not Path(path).is_relative_to(output):raise ValueError('注册回执路径不在本次输出根')
    content = execute(prefix, ['-c', 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text())', path],
                  attempt / 'registration-receipt')
    receipt = json.loads(content)
    if (receipt.get('schema_version') != '1.0' or receipt.get('task_id') != 'vllm-model-register'
            or receipt.get('status') != 'completed' or receipt.get('conclusions', {}).get('registration') != 'pass'
            or receipt.get('details', {}).get('action') != 'checked'):
        raise ValueError('最终载体注册复验不是有效的本次completed/pass')
    write_json(attempt / 'registration-manifest.json', receipt)
    return receipt
