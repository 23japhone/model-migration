"""在实际 EvalScope 解释器内解析同一 scope 的全部子集、原生样本与有效配置。"""
import os
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from precision.evidence import load, sha256, write
from precision.quality.budget import inspect as inspect_budget
from precision.quality.protocol import check_config, score_only
from precision.runtime.evalscope.configuration import load_config, native_config, validate
from precision.runtime.evalscope.data import bind_config
from precision.runtime.evalscope.probe import environment_identity, prepare_task
from precision.runtime.versioning import inspect_environment


def freeze(payload, path):
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    request = payload['request']
    ev = request['measurement']['evaluator']
    source = ev['config']
    if sha256(source['path']) != source['sha256']:
        raise ValueError('原始评测配置在准备后发生变化')
    attempt = Path(payload['attempt'])
    raw = load_config(source['path'])
    score_only(raw)
    tasks = request['measurement']['tasks']
    validate(raw, tasks, payload['handoff'], allow_native_budget=True)
    version = inspect_environment('evalscope', ev, attempt)
    environment, validation = environment_identity()
    files = {}
    prepared, adapters = [], []
    for task in tasks:
        item, protocol, _ = prepare_task(raw, task, {**payload, 'asset': payload['assets'][task['subset']]},
                                         files, environment)
        item.pop('capture_required', None)
        prepared.append(item)
        adapters.append(protocol)
    combined = bind_config(raw, payload['assets'][tasks[0]['subset']])
    combined.update(collect_perf=False, debug=False, work_dir=str(attempt / 'scope' / 'evalscope'), no_timestamp=True,
                    dataset_dir=str(attempt / 'dataset-cache'))
    resolved = native_config(combined).model_dump(mode='json')
    resolved['api_key'] = raw.get('api_key', 'EMPTY')
    revisions = {adapter['evaluation_version'] or version['installed_version'] for adapter in adapters}
    if revisions != {request['protocol']['task_revision']}:
        raise ValueError('实际原生任务 evaluation_version 与冻结的 task_revision 不一致')
    adapter_sources = set(environment['configuration_sources'].values())
    if len(adapter_sources) != 1:
        raise ValueError('无法确认同一 scope 的唯一实际 adapter 源码')
    check_config(request['protocol'], raw, resolved, request.get('scoring_proof'), adapter_sources.pop())
    if request.get('scoring_proof'):
        proof = request['scoring_proof']
        files[proof['path']] = proof['sha256']
    budget = inspect_budget(raw, prepared, payload)
    for name, value in [('effective-config', resolved), ('original-config', raw), ('environment', environment)]:
        frozen = attempt / (name + '.json')
        write(frozen, value)
        files[str(frozen)] = sha256(frozen)
    write(path, {'tasks': prepared, 'benchmark_conditions': adapters, 'files': files,
                 'model_files': payload['handoff']['model']['files'],
                 'version': version['installed_version'], 'validation_files': validation, 'prompt_budget': budget})


if __name__ == '__main__':
    freeze(load(sys.argv[1]), sys.argv[2])
