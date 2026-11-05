"""inspect-devices：只读复核已交接服务，不调用任何模型生命周期效果。"""
import copy

from device_contract import bound, load, require
from . import mapping
from .. import outputs
from ..evidence import begin, safe_text
from ..lifecycle import complete
from ..runtime import carrier, inspection


def inspect(request, target, run_id):
    handoff = bound(request['handoff'])
    source = bound(handoff['declared_request']) if 'declared_request' in handoff else handoff
    outputs.check(target, [*outputs.protected_roots(source), request['handoff']['path']])
    attempt, manifest = begin(target, request, run_id, 'inspect-devices')
    manifest['conclusions'] = {'devices': 'not_evaluated'}
    manifest['uncovered'] = ['本次只读检查设备映射与进程归属，不刷新功能或精度结论、不启停模型']
    status = 'blocked'
    try:
        declared, recorded, runtime = load(handoff)
        execution = copy.deepcopy(declared)
        execution.update(runtime=runtime, _device_state=recorded)
        resource = handoff['resource']
        require(recorded['host'] == mapping.execution_host(resource['kind'], attempt), '宿主启动身份变化')
        carrier.validate(resource, attempt)
        workers = inspection.running_evidence(execution, resource, attempt)
        require(carrier.worker_signature(handoff) == carrier.worker_signature({'workers': workers}),
                'API/worker PID 或启动身份与交接不同')
        evidence = mapping.capture(execution, resource, attempt, workers)
        carrier.validate(resource, attempt)
        inspection.confirm_workers(workers, resource)
        manifest['detailed_report'] = {'device_evidence': evidence, 'workers': workers}
        manifest['evidence'] = {key: evidence[key]['path'] for key in ('device_mapping', 'effective_runtime')}
        manifest['conclusions']['devices'] = 'pass'
        status = 'completed'
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as error:
        manifest['diagnostic'] = {'type': type(error).__name__, 'message': safe_text(error)}
        if isinstance(error, KeyboardInterrupt):
            status = 'failed'
    return complete(manifest, status)
