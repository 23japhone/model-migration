"""最终资源交接与取消收束；只恢复一次已授权的精确身份清理。"""
from contextlib import contextmanager
import json
import signal

from .evidence import finish as save_result, safe_text, sha256, write_json
from .runtime import carrier

ERRORS = (OSError, ValueError, RuntimeError, KeyError)


def diagnostic(error):
    return {'type': type(error).__name__, 'message': safe_text(error) or '本次操作被取消'}


@contextmanager
def defer_interrupts(received):
    previous = {}
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda signum, frame: received.append(signum))
        yield
    finally:
        for number, handler in previous.items():signal.signal(number, handler)


def settle(effect, recover):
    outcome = {'value': None, 'cancellation': None, 'error': None, 'deferred_signals': []}
    try:outcome['value'] = effect()
    except KeyboardInterrupt as error:
        outcome['cancellation'] = diagnostic(error)
        try:
            with defer_interrupts(outcome['deferred_signals']):outcome['value'] = recover()
        except (*ERRORS, KeyboardInterrupt) as error:outcome['error'] = diagnostic(error)
    except ERRORS as error:outcome['error'] = diagnostic(error)
    return outcome


def cleanup(request, resource, attempt, manifest, failed):
    if resource is None:resource = carrier.recover(attempt)
    if resource is None:return
    manifest['detailed_report']['resource'] = resource
    if failed and resource['ownership'] == 'owned':
        try:carrier.failure_logs(resource, attempt)
        except ERRORS as error:manifest['detailed_report']['log_capture_error'] = safe_text(error)
    if resource['ownership'] == 'owned' and (failed or request['service']['retention'] == 'stop'):
        final = carrier.stop(resource, attempt)
    else:
        carrier.validate(resource, attempt)
        final = {'state': 'retained', 'resource': resource}
    manifest['detailed_report']['final_service'] = final
    write_json(attempt / 'service-final.json', final)
    handoff = attempt / 'deployment-handoff.json'
    if handoff.exists():
        value = json.loads(handoff.read_text());value['final_service'] = final
        write_json(handoff, value)
        manifest['detailed_report']['handoff_sha256'] = sha256(handoff)
    manifest['conclusions']['lifecycle'] = 'pass'


def finalize(request, resource, attempt, manifest, failed):
    outcome = settle(lambda: cleanup(request, resource, attempt, manifest, failed),
                     lambda: cleanup(request, resource, attempt, manifest, True))
    manifest['detailed_report']['cleanup'] = {k: v for k, v in outcome.items() if k != 'value'}
    if outcome['cancellation']:
        manifest.setdefault('diagnostic', outcome['cancellation'])
    if outcome['error']:
        manifest['conclusions']['lifecycle'] = 'fail'
        manifest['detailed_report']['cleanup_error'] = outcome['error']
        manifest.setdefault('diagnostic', outcome['cancellation'] or outcome['error'])
    return not outcome['cancellation'] and not outcome['error']


def stop(resource, attempt, manifest):
    effect = lambda: carrier.stop(resource, attempt)
    outcome = settle(effect, effect)
    manifest['detailed_report'] = {**(outcome['value'] or {'state': 'cleanup_unconfirmed'}),
                                   'cleanup': {k: v for k, v in outcome.items() if k != 'value'}}
    manifest['conclusions'] = {'lifecycle': 'fail' if outcome['error'] else 'pass'}
    if outcome['cancellation'] or outcome['error']:
        manifest['diagnostic'] = outcome['cancellation'] or outcome['error']
        return 'failed'
    return 'completed'


def complete(manifest, status):
    received = []
    with defer_interrupts(received):
        result = save_result(manifest, status)
        if received:
            manifest.setdefault('diagnostic', {'type': 'KeyboardInterrupt', 'message': '终态写入期间取消'})
            manifest['detailed_report']['terminal_cancellation'] = list(received)
            result = save_result(manifest, 'failed')
    return result
