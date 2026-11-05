"""部署客户操作公开入口；每次操作留独立身份与效果回执。"""
import argparse
import json
from pathlib import Path
import uuid
import signal

from ..outputs import target
from ..evidence import write_json, safe_text
from . import npu_capacity, image_probe, inspection, runtime_paths, functional_inputs


def cancelled(signum, frame):
    raise KeyboardInterrupt('部署观察已取消，需复核本次资源')


def blocked(error):
    report = {'status': 'failed' if isinstance(error, KeyboardInterrupt) else 'blocked', 'reason': safe_text(error)}
    if getattr(error, 'diagnostic', None):
        report['diagnostic'] = error.diagnostic
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    handlers = {sig: signal.signal(sig, cancelled) for sig in (signal.SIGINT, signal.SIGTERM)}
    attempt = None
    try:
        request = json.loads(args.request.read_text())
        operation = request['operation']
        if operation not in ('observe', 'image', 'allocate', 'replace_container', 'runtime_paths', 'functional_inputs'):
            raise ValueError('未知客户部署操作')
        protected = [args.request.resolve()]
        if operation == 'functional_inputs':
            protected.append(Path(request['model_path']))
        parent = target(args.work_root, args.run_id, protected) / 'customer'
        if args.plan:
            print(json.dumps({'status': 'not_run', 'operation': operation}))
            return 0
        attempt = parent / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        write_json(attempt / 'request.json', request)
        operations = {'observe': inspection.host, 'image': image_probe.inspect, 'replace_container': inspection.replace,
                      'runtime_paths': runtime_paths.capture, 'functional_inputs': functional_inputs.inspect,
                      'allocate': lambda value, folder: npu_capacity.allocate(value)}
        result = operations[operation](request, attempt)
        report = {'status': 'completed', 'operation': operation, 'inputs': request, 'result': result}
        path = attempt / 'observation.json'
        write_json(path, report)
        print(json.dumps({**report, 'manifest': str(path)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as error:
        report = blocked(error)
        if attempt is not None:
            report['manifest'] = str(attempt / 'observation.json')
            write_json(attempt / 'observation.json', report)
        print(json.dumps(report, ensure_ascii=False))
        return 1 if isinstance(error, KeyboardInterrupt) else 3
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
