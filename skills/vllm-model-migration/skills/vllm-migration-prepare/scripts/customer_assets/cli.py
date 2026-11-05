"""明确准备操作的独立回执；不触达服务和 NPU。"""
import argparse
import json
from pathlib import Path
import uuid
import signal
import subprocess

from migration_prepare.results import output_parent, write_json, safe_text
from evalscope_installation import normalize, protect, legacy
from . import environment, evaluator, image


def cancelled(signum, frame):
    raise KeyboardInterrupt('客户资产准备已取消，保留部分资产')


def validate(value, parent):
    if not isinstance(value, dict):
        raise ValueError('客户准备请求必须是对象')
    allowed = {'environment': {'operation', 'packages', 'versions'},
               'evalscope_environment': {'operation', 'install'},
               'image': {'operation', 'reference', 'platform'}}
    operation = value.get('operation')
    if operation not in allowed or value.keys() - allowed[operation]:
        raise ValueError('未知客户准备操作或字段')
    if operation == 'evalscope_environment':
        protect(normalize(value.get('install', {})), [parent.parent.parent])
        legacy(parent)
    return operation


def perform(value, parent, attempt):
    if value['operation'] == 'evalscope_environment':
        return evaluator.install(value, attempt)
    if value['operation'] == 'environment':
        return environment.prepare(environment.selection(value, parent, attempt), parent, attempt)
    return image.resolve(value['reference'], value['platform'], attempt)


def failure_report(error):
    report = {'status': 'failed' if isinstance(error, KeyboardInterrupt) else 'blocked', 'reason': safe_text(error)}
    state = getattr(error, 'selection_frozen', None)
    if type(state) is bool:
        report['selection_frozen'] = state
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
        value = json.loads(args.request.read_text())
        parent = output_parent(args.work_root, args.run_id) / 'customer-assets'
        operation = validate(value, parent)
        source = args.request.resolve()
        if operation == 'evalscope_environment':
            protect(normalize(value.get('install', {})), [source])
        if source.is_relative_to(parent) or parent.is_relative_to(source):
            raise ValueError('准备输出与请求来源重叠')
        if args.plan:
            print(json.dumps({'status': 'not_run', 'operation': operation, 'network': 'not_used'}))
            return 0
        attempt = parent / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        write_json(attempt / 'request.json', value)
        result = perform(value, parent, attempt)
        report = {'status': 'not_run' if result.get('status') == 'not_run' else 'completed',
                  'operation': operation, 'inputs': value, 'result': result}
        write_json(attempt / 'resolution.json', report)
        print(json.dumps({**report, 'manifest': str(attempt / 'resolution.json')}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, KeyboardInterrupt, subprocess.TimeoutExpired) as error:
        report = failure_report(error)
        if attempt is not None:
            report['manifest'] = str(attempt / 'resolution.json')
            write_json(attempt / 'resolution.json', report)
        print(json.dumps(report, ensure_ascii=False))
        return 1 if isinstance(error, KeyboardInterrupt) else 3
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
