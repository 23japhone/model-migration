"""部署阶段依赖台账与终态聚合；独立用例由其领域所有者隔离。"""
import copy
from .evidence import begin, safe_text, sha256, write_json
from .inputs import collect
from .protocol.cases import conclusions, execute
from .runtime import carrier
from .lifecycle import complete, finalize

PHASES = ('inputs', 'carrier', 'devices', 'runtime', 'registration', 'service', 'cases', 'final_handoff')

UNCOVERED = ['未做正式数据集精度评分或性能验收', '音频、视频、graph、量化、MTP与多机未验收',
             '本次结果只覆盖显式版本、模型、硬件、配方及用例，不外推其他组合']


def record(manifest, attempt, phase):
    manifest['detailed_report']['stage'] = phase
    manifest['detailed_report'].setdefault('ledger', []).append({'step': phase, 'status': 'running'})
    write_json(manifest['report_path'], manifest)


def completed(manifest):
    manifest['detailed_report']['ledger'][-1]['status'] = 'succeeded'


def block_dependents(manifest):
    ledger = manifest['detailed_report'].get('ledger', [])
    seen = {entry['step'] for entry in ledger}
    ledger.extend({'step': name, 'status': 'blocked', 'reason': '必需前置未完成，详见原始诊断'}
                  for name in PHASES if name not in seen)


def run(request, target, run_id):
    attempt, manifest = begin(target, request, run_id, 'verify')
    request = copy.deepcopy(request)
    manifest['uncovered'] = list(UNCOVERED)
    manifest['conclusions'] = {k: 'not_evaluated' for k in
                              ['inputs', 'runtime', 'registration', 'workers', 'functional_protocol', 'semantic', 'lifecycle']}
    resource = None
    fatal = False
    status = 'failed'
    try:
        record(manifest, attempt, 'inputs')
        inputs = collect(request, attempt)
        manifest['evidence']['inputs'] = str(attempt / 'inputs.json')
        if inputs['errors']:raise ValueError('必需运行输入不完整，详见输入证据')
        completed(manifest);manifest['conclusions']['inputs'] = 'pass'
        request['_architectures'] = inputs['model']['architectures']
        record(manifest, attempt, 'carrier')
        resource = carrier.prepare(request, attempt)
        completed(manifest)
        record(manifest, attempt, 'devices')
        request = carrier.resolve_devices(request, resource, attempt)
        completed(manifest)
        record(manifest, attempt, 'runtime')
        manifest['detailed_report']['runtime_checks'] = carrier.inspect_runtime(request, resource, attempt)
        manifest['conclusions']['runtime'] = 'pass'
        completed(manifest)
        record(manifest, attempt, 'registration')
        manifest['detailed_report']['registration'] = carrier.check_registration(request, resource, attempt)
        manifest['conclusions']['registration'] = 'pass'
        completed(manifest)
        record(manifest, attempt, 'service')
        carrier.launch(request, resource, attempt)
        handoff = carrier.capture(request, resource, attempt)
        manifest['evidence']['handoff'] = str(attempt / 'deployment-handoff.json')
        manifest['detailed_report']['handoff_sha256'] = sha256(manifest['evidence']['handoff'])
        manifest['conclusions']['workers'] = 'pass'
        completed(manifest)
        record(manifest, attempt, 'cases')
        results = execute(request, attempt)
        manifest['detailed_report']['cases'] = results
        manifest['evidence']['cases'] = str(attempt / 'cases.json')
        manifest['conclusions'].update(conclusions(results))
        completed(manifest)
        record(manifest, attempt, 'final_handoff')
        manifest['conclusions']['workers'] = 'not_evaluated'
        carrier.final_handoff(request, resource, attempt, handoff)
        manifest['conclusions']['workers'] = 'pass'
        completed(manifest)
        status = 'completed'
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, KeyboardInterrupt) as error:
        fatal = True
        manifest['diagnostic'] = {'type': type(error).__name__, 'message': safe_text(error) or '本次执行被取消'}
        manifest['diagnostic'].update(getattr(error, 'diagnostic', {}))
        status = 'blocked' if isinstance(error, ValueError) else 'failed'
        if manifest['detailed_report'].get('ledger'):
            manifest['detailed_report']['ledger'][-1].update(status='failed', diagnostic=manifest['diagnostic'])
        block_dependents(manifest)
    finally:
        if not finalize(request, resource, attempt, manifest, fatal):status = 'failed'
    return complete(manifest, status)
