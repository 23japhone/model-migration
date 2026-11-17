"""目标环境执行的中文公开CLI；无需维护后端或SSH调度。"""
import argparse
import json
from pathlib import Path
import re
import signal
import sys

from . import request as inputs
from . import outputs
from .evidence import safe_text
from .runtime.capture import action as capture_action
from .runtime.capture import session as capture_session
from .runner import run
from .review import review, stop
from .devices.recheck import inspect as inspect_devices


class ChineseParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:\n', '参数：', 1)

    def error(self, message):
        self.exit(2, '参数无效，请使用 --help 核对输入。\n')


def action_request(path, action, work_root, run_id):
    if action == 'verify':
        request = inputs.load(path)
        return request, inputs.output_target(request, work_root, run_id)
    request = json.loads(Path(path).read_text())
    if action == 'capture-session':
        capture_session.validate(request)
        return request, outputs.target(work_root, run_id)
    if action in ('capture-one', 'capture-read'):
        capture_action.validate(request, action)
        return request, outputs.target(work_root, run_id)
    required = ['source', 'cases'] if action == 'review' else ['handoff']
    inputs.fields(request, required)
    item = request['source' if action == 'review' else 'handoff']
    inputs.fields(item, ['path', 'sha256'])
    inputs.absolute(item['path'])
    if action == 'review':
        if not isinstance(request['cases'], dict):raise ValueError('复核用例须为对象')
        for judgment in request['cases'].values():
            inputs.fields(judgment, ['verdict', 'reason', 'response_sha256'])
    if not inputs.HASH.fullmatch(item['sha256']):raise ValueError('回执摘要无效')
    return request, outputs.target(work_root, run_id)


def cancelled(signum, frame):
    raise KeyboardInterrupt('收到终止信号')


def main():
    parser = ChineseParser(description='核验运行载体、部署及真实功能；独立语义复核或精确停止owned资源', add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示帮助')
    parser.add_argument('--request', required=True, type=Path, help='本地JSON请求')
    parser.add_argument('--work-root', required=True, type=Path, help='包外运行证据根')
    parser.add_argument('--run-id', required=True, help='本次运行标识')
    parser.add_argument('--plan', action='store_true', help='仅检查形状与路径，不读目标资产、构建、启动或写目录')
    parser.add_argument('--action', choices=['verify', 'review', 'stop', 'inspect-devices', 'capture-one', 'capture-read', 'capture-session'], default='verify', help='部署验证、语义复核、停止、设备复核、单请求采集、采集会话或离线回查')
    args = parser.parse_args()
    try:
        request, target = action_request(args.request, args.action, args.work_root, args.run_id)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('输入无效：' + safe_text(error), file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps({'status': 'not_run', 'operation': args.action, 'request': request}, ensure_ascii=False))
        return 0
    operation = {'capture-session': capture_session.run, 'capture-one': capture_action.run, 'capture-read': capture_action.lookup,
                 'verify': run, 'review': review, 'stop': stop, 'inspect-devices': inspect_devices}[args.action]
    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        code, result = operation(request, target, args.run_id)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as error:
        print('执行或证据未完整保存，不能报告成功：' + safe_text(error), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, ensure_ascii=False))
    return code
