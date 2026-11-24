"""在已核验的载体中准备每次部署独有的配置，并封存宿主机证据。"""
import json
import os
from pathlib import Path
import uuid

from ... import outputs
from ...evidence import safe_text, sha256, write_json
from .. import execution
from .launch import stage
from .contract import check_service, native_config


def shadows_attempt(dest, mount_target, target):
    return ((dest.is_relative_to(mount_target) and target.is_relative_to(dest))
            or dest.is_relative_to(target))


def runtime_root(host, request):
    if request['runtime']['kind'] == 'process':
        return host
    mounts = request['service']['docker']['mounts']
    matches = [m for m in mounts if host.is_relative_to(Path(m['source']))]
    if len(matches) != 1 or matches[0]['read_only']:
        raise ValueError('输出目录需要唯一、可写的显式 Docker 挂载')
    mount = matches[0]
    target = Path(mount['target']) / host.relative_to(mount['source'])
    for other in mounts:
        if other is mount:
            continue
        dest = Path(other['target'])
        if shadows_attempt(dest, Path(mount['target']), target):
            raise ValueError('其他 Docker 挂载遮蔽或重叠采集路径')
    return target


def check_target(request, resource, attempt, payload):
    runtime = request['runtime']
    code = Path(__file__).with_name('probe.py').read_text()
    raw = execution.execute(execution.prefix(request, resource), ['-B', '-c', code],
                            attempt / 'worker-capture-probe', input_text=json.dumps(payload), timeout=60,
                            env={**os.environ, **runtime['env']},
                            cwd=runtime.get('cwd') if resource['kind'] == 'process' else None)
    prefix = 'VLLM_CAPTURE_JSON='
    rows = [line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)]
    if len(rows) != 1:
        raise ValueError('目标采集准备探针缺少唯一回执')
    result = json.loads(rows[0])
    if not isinstance(result, dict):
        raise ValueError('目标采集准备探针回执无效')
    return result


def prepare_files(request, attempt, receipt):
    check_service(request)
    host = Path(request['worker_capture']['output_root'])
    outputs.check(host, outputs.protected_roots(request))
    folder = host / attempt.name
    runtime = runtime_root(folder, request)
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    if len(request['npu']['devices']) == 2:
        mapping = request['_device_state']['devices']
        parallel = folder / 'parallel.json'
        write_json(parallel, {'world_size': 2, 'devices': mapping})
        receipt['parallel'] = {'path': str(parallel), 'sha256': sha256(parallel)}
    dump = folder / 'dump'
    dump.mkdir(mode=0o700)
    config = folder / 'msprobe.json'
    from .aclgraph import config as graph
    if request['worker_capture']['adapter'] == graph.ADAPTER:
        receipt['graph'] = graph.settings(request)
        write_json(folder / 'graph.json', receipt['graph'])
        write_json(config, graph.native_config(runtime / 'dump' / 'native'))
    else:
        write_json(config, native_config(runtime / 'dump'))
    receipt['config'] = {'path': str(config), 'runtime_path': str(runtime / config.name), 'sha256': sha256(config)}
    receipt['dump'] = {'path': str(dump), 'runtime_path': str(runtime / 'dump')}


def record_cache_config(receipt, cache, observation):
    path = Path(receipt['config']['path']).with_name('decode-cache.json')
    result = observation.get('decode_cache', {'status': 'unavailable', 'reason': '缺少目标能力回执'})
    write_json(path, {**cache, **result})
    receipt['decode_cache'] = {'path': str(path), 'sha256': sha256(path)}


def verify_files(request, resource, attempt, receipt, executable):
    config = receipt['config']
    marker = '.verify-' + uuid.uuid4().hex
    challenge = uuid.uuid4().hex
    payload = {'config': config['runtime_path'], 'config_sha256': config['sha256'],
               'dump_path': receipt['dump']['runtime_path'],
               'marker': marker, 'challenge': challenge, 'world_size': len(request['npu']['devices']),
               'expected_msprobe_version': request['worker_capture'].get('expected_msprobe_version')}
    cache = request['worker_capture'].get('decode_cache')
    observation = check_target(request, resource, attempt, {**payload, 'decode_cache': bool(cache), 'graph': receipt.get('graph')})
    if cache:
        try:
            record_cache_config(receipt, cache, observation)
        except OSError as error:
            observation['decode_cache'] = {'status': 'unavailable', 'reason': safe_text(error)}
    receipt['probe'] = observation
    if 'error' in observation:
        observation['error'] = safe_text(observation['error'])
        raise ValueError(observation['error'])
    if not observation.get('msprobe_version') or observation.get('config_sha256') != config['sha256']:
        raise ValueError('目标解释器缺少版本或配置摘要证据')
    if observation.get('executable_realpath') != executable:
        raise ValueError('采集探针与已验证运行环境的解释器不一致')
    marker_path = Path(receipt['dump']['path']) / marker
    if not marker_path.is_file() or marker_path.is_symlink() or marker_path.read_text(encoding='ascii') != challenge:
        raise ValueError('宿主机未读回本次目标写入的校验内容')
    marker_path.unlink()
    receipt['write_readback'] = True


def prepare(request, resource, attempt, executable):
    receipt = {'status': 'unavailable', 'reason': '采集准备中断'}
    request['_capture_preparation'] = receipt
    try:
        prepare_files(request, attempt, receipt)
        verify_files(request, resource, attempt, receipt, executable)
        stage(receipt)
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error.__cause__, KeyboardInterrupt):
            raise KeyboardInterrupt('采集探针取消且目标清理未确认') from error
        receipt['reason'] = safe_text(error)
    else:
        receipt.update(status='prepared', reason='目标解释器、配置摘要和输出目录读写核验通过')
    return receipt


def publish(request, attempt, manifest):
    if 'worker_capture' not in request:
        return
    reason = manifest.get('diagnostic', {}).get('message', '部署前置检查未完成')
    result = request.get('_capture_preparation', {'status': 'not_attempted', 'reason': reason})
    capture = request['worker_capture']
    receipt = {'schema_version': '1.0', 'run_id': manifest['run_id'], 'attempt_id': attempt.name,
               'adapter': capture['adapter'], 'output_root': capture['output_root'],
               'requested_python': request['runtime']['python'], **result}
    if 'expected_msprobe_version' in capture:
        receipt['expected_msprobe_version'] = capture['expected_msprobe_version']
    path = attempt / 'worker-capture-preparation.json'
    write_json(path, receipt)
    manifest['detailed_report']['worker_capture'] = {
        'status': result['status'], 'reason': result['reason'], 'receipt': {'path': str(path), 'sha256': sha256(path)}}
