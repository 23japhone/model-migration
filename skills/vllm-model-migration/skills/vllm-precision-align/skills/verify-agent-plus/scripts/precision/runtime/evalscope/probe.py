"""在 EvalScope 解释器内冻结有效配置、原生样本和评测环境身份。"""
import importlib.metadata
from pathlib import Path
import sys

from ...evidence import sha256, write
from ..versioning import inspect_environment
from .configuration import load_config, validate
from .data import native_task


def environment_identity():
    distribution = importlib.metadata.distribution('evalscope')
    sources = {}; validation = {}
    for relative in distribution.files or []:
        if str(relative).startswith('evalscope/') and str(relative).endswith('.py'):
            path = Path(distribution.locate_file(relative)).resolve()
            sources[str(relative)] = sha256(path); validation[str(path)] = sources[str(relative)]
    if not sources:
        raise ValueError('无法固定 EvalScope 实际源码')
    packages = {d.metadata['Name'].lower().replace('_', '-'): d.version
                for d in importlib.metadata.distributions() if d.metadata.get('Name')}
    return {'python': sys.version.split()[0], 'packages': dict(sorted(packages.items())),
            'sources': sources, 'configuration_sources': {}}, validation


def prepare_task(raw, task, payload, files, environment):
    attempt = Path(payload['attempt'])
    config, adapter, selection = native_task(raw, task, payload['asset'], attempt)
    module = sys.modules[type(adapter).__module__]
    module_path = Path(module.__file__).resolve()
    environment['configuration_sources'][module.__name__] = sha256(module_path)
    resolved = config.model_dump(mode='json'); resolved['api_key'] = raw.get('api_key', 'EMPTY')
    path = attempt / ('frozen-' + task['dataset'] + '.json'); write(path, resolved)
    files[str(path)] = sha256(path)
    samples_path = attempt / ('samples-' + task['dataset'] + '.json'); write(samples_path, selection['samples'])
    files[str(samples_path)] = sha256(samples_path)
    protocol = {'benchmark': task['benchmark'], 'subset': task['subset'],
                'evaluation_version': adapter.benchmark_meta.evaluation_version,
                'config': adapter.to_dict(), 'reader_samples_sha256': selection['reader_samples_sha256']}
    protocol['config'].pop('dataset_id', None)
    prepared = {**task, 'backend': 'evalscope', 'config': str(path),
                'sample_snapshot': {'path': str(samples_path), 'sha256': files[str(samples_path)]},
                'output_root': str(attempt / 'tasks' / task['dataset']), 'capture_required': True,
                'endpoint': payload['handoff']['endpoint'], 'served_model': payload['handoff']['model']['name'],
                'max_model_len': payload['service_snapshot']['model']['max_model_len']}
    return prepared, protocol, resolved


def model_conditions(resolved):
    keys = ('model_task', 'chat_template', 'generation_config', 'repeats', 'seed', 'judge', 'model_args')
    return {key: resolved.get(key) for key in keys}


def freeze(payload, output):
    request = payload['request']; source = Path(request['evaluator']['config']['path'])
    version_check = inspect_environment('evalscope', request['evaluator'], payload['attempt'])
    if sha256(source) != request['evaluator']['config']['sha256']:
        raise ValueError('配置在探测前发生变化')
    raw = load_config(source); validate(raw, request['tasks'], payload['handoff'])
    attempt = Path(payload['attempt'])
    files = {str(path): sha256(path) for path in attempt.glob('evaluator-*.json')}
    archive = attempt / ('source-config' + source.suffix); archive.write_bytes(source.read_bytes())
    files[str(archive)] = sha256(archive)
    environment, validation = environment_identity()
    prepared = []; datasets = []; resolved = None
    for task in request['tasks']:
        item, protocol, resolved = prepare_task(raw, task, payload, files, environment)
        prepared.append(item); datasets.append(protocol)
    identity_path = attempt / 'evaluator-identity.json'; write(identity_path, environment)
    files[str(identity_path)] = sha256(identity_path)
    identity = payload['identity']; snapshot = payload['service_snapshot']
    conditions = {key: identity[key] for key in ('model_files', 'weight_files', 'dataset', 'selection', 'server_generation_files')}
    conditions.update(datasets=datasets, model=model_conditions(resolved), evaluator_backend='evalscope',
                      evaluator_version=version_check['installed_version'], evaluator_identity=environment,
                      server_generation=snapshot['generation'], max_model_len=snapshot['model']['max_model_len'],
                      vllm_version=snapshot['model']['version'])
    write(output, {'tasks': prepared, 'conditions': conditions, 'files': files, 'validation_files': validation,
                   'environment': {'python': sys.version, 'executable': sys.executable,
                                   'version': version_check['installed_version'], 'version_check': version_check}})
