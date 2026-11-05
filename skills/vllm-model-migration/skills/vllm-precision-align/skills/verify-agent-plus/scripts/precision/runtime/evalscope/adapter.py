"""通过独立解释器执行配置探测和原生 run_task。"""
from pathlib import Path

from ...evidence import load, write
from ..capture import recording
from ..process import execute as run_process
from .completion import validate_response


def prepare(request, inputs, attempt):
    payload = attempt / 'probe-request.json'
    write(payload, {'request': request, 'handoff': inputs['handoff'], 'asset': inputs['asset'],
                    'identity': inputs['identity'], 'service_snapshot': inputs['service_snapshot'],
                    'attempt': str(attempt)})
    output = attempt / 'config-index.json'
    run_process([request['evaluator']['python'], '-I', '-B', str(Path(__file__).with_name('entry.py')),
                 'probe', str(payload), str(output)], attempt / 'configuration', 60)
    return load(output)


def execute(request, task, attempt):
    root = Path(task['output_root']); root.mkdir(parents=True, exist_ok=False)
    timeout = request['evaluator'].get('timeout_seconds', 14400)
    with recording(task['endpoint'], root / 'http-capture', min(timeout, 3600), validate_response) as endpoint:
        config = load(task['config'])
        config.update(api_url=endpoint + '/v1', work_dir=str(root / 'evalscope'), no_timestamp=True)
        path = root / 'execution.json'; write(path, config)
        argv = [request['evaluator']['python'], '-I', '-B', str(Path(__file__).with_name('entry.py')), 'run', str(path)]
        return run_process(argv, attempt / ('process-' + task['dataset']), timeout, check=False)
