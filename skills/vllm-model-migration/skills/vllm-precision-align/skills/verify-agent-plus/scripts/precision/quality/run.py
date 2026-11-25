"""质量测量由一个合并 scope 的 EvalScope 进程交付，前后各核对运行身份。"""
from pathlib import Path
import subprocess

from ..contract import check_output
from ..evidence import begin, finish, load, record, verify_files, write
from ..inputs import collect
from ..runtime import service
from ..runtime.process import execute
from . import runtime, static_gate
from .native import measure
from .protocol import check_assets, ref, stable_ids, subset_assets

ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError)


def project(attempt, manifest, protocol, result):
    path = attempt / 'quality-evidence.json'
    value = {'format': 'task-quality-native-v1', 'hardware': 'npu', 'protocol': protocol, 'scores': result['scores'],
             'static_conditions': result.get('static_conditions')}
    write(path, value)
    manifest['evidence']['quality_evidence'] = ref(path)
    manifest['conclusions']['evaluation_validity'] = 'pass'


def fail(manifest, error, status):
    manifest['diagnostic'] = {'type': type(error).__name__, 'message': str(error)}
    if isinstance(error, static_gate.Mismatch):
        manifest['diagnostic']['code'] = 'quality_static_mismatch'
        manifest['detailed_report']['formal_requests'] = 0
    manifest['conclusions']['evaluation_validity'] = 'fail' if status == 'failed' else 'not_evaluated'
    return finish(manifest, status)


def freeze(request, inputs, assets, attempt, before):
    path = attempt / 'quality-probe.json'
    write(path, {'request': request, 'handoff': inputs['handoff'], 'assets': assets, 'attempt': str(attempt),
                 'identity': inputs['identity'], 'service_snapshot': before})
    output = attempt / 'quality-index.json'
    ev = request['measurement']['evaluator']
    execute([ev['python'], '-I', '-B', str(Path(__file__).with_name('probe.py')),
             str(path), str(output)], attempt/'configuration', 120)
    index = load(output)
    if index['version'] != request['protocol']['evaluator_version']:
        raise ValueError('实际评测器版本与冻结协议不同')
    return index


def run_native(measurement, attempt):
    evaluator = measurement['evaluator']
    entry = Path(__file__).resolve().parents[1] / 'runtime/evalscope/entry.py'
    argv = [evaluator['python'], '-I', '-B', str(entry), 'run', str(attempt/'effective-config.json')]
    return execute(argv, attempt/'process-scope', evaluator.get('timeout_seconds', 14400), check=False)


def run(request, parent, run_id):
    measurement = request['measurement']
    try:
        inputs = collect(measurement)
        assets = subset_assets(request)
        protected = inputs['protected'] + [a['path'] for a in assets.values()]
        check_output(parent, protected)
        check_assets(request['protocol'], inputs['identity']['model_files'], assets)
    except ERRORS as error:
        report = record(request, run_id, 'quality-run', 'blocked')
        report['summary'] = str(error)
        return 3, report
    attempt, manifest = begin(parent, request, run_id, 'quality-run')
    details = manifest['detailed_report']
    details.update(hardware='npu', protected_roots=protected)
    try:
        stable = {name: stable_ids(asset) for name, asset in assets.items()}
        before = service.snapshot(inputs, measurement, attempt, 'before')
        index = freeze(request, inputs, assets, attempt, before)
        static = runtime.recorded(index, before)
        static_gate.check(request, static)
        scope = str(attempt/'scope')
        details.update(tasks=index['tasks'], stable_samples=stable, scope_root=scope,
                       prompt_budget=index['prompt_budget'])
    except ERRORS as error:
        return fail(manifest, error, 'blocked')
    try:
        receipt = run_native(measurement, attempt)
        if receipt['returncode'] != 0:
            raise ValueError('EvalScope 原生进程失败，详见 process-scope')
        measured = measure(scope, index['tasks'], stable, request['scores'])
        files = {**index['files'], **measured.pop('files')}
        for path in ('quality-index.json','process-scope/process.json',
                     'service-before.json'):
            item = ref(attempt / path)
            files[item['path']] = item['sha256']
        verify_files({**files, **index['validation_files']})
        after = service.snapshot(inputs, measurement, attempt, 'after')
        if after != before or collect(measurement)['identity'] != inputs['identity'] or subset_assets(request) != assets:
            raise ValueError('测量期间服务、模型或数据发生变化')
        after_ref = ref(attempt/'service-after.json')
        files[after_ref['path']] = after_ref['sha256']
        details.update(files=files, context_validation='pass', formal_requests=measured['formal_requests'],
                       request_count_source=measured['request_count_source'])
        measured['static_conditions'] = static
        project(attempt, manifest, request['protocol'], measured)
        return finish(manifest, 'completed')
    except (*ERRORS, KeyboardInterrupt) as error:
        return fail(manifest, error, 'failed')
