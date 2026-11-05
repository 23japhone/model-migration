"""目标环境中文 CLI；只读源码、模型和资料，证据留在包外。"""
import argparse
import json
from pathlib import Path
import signal
import sys

from . import contract
from .evidence import read_json
from .runner import run
from .redaction import text


class ChineseParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:\n', '参数：', 1)

    def error(self, message):
        self.exit(2, '参数无效，请使用 --help 核对输入。\n')


def cancelled(signum, frame):
    raise KeyboardInterrupt('收到取消信号 ' + str(signum))


def main():
    parser = ChineseParser(description='采集模型适配上下文，核验源码改动、权重映射和定向证据', add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示帮助')
    parser.add_argument('--request', type=Path, required=True, help='JSON 请求绝对路径')
    parser.add_argument('--work-root', type=Path, required=True, help='包外证据根')
    parser.add_argument('--run-id', required=True, help='本次运行标识')
    parser.add_argument('--action', choices=['capture', 'check'], default='capture', help='冻结上下文或核验适配交接')
    parser.add_argument('--plan', action='store_true', help='只检查请求形状和输出保护，不读目标资料或写运行目录')
    args = parser.parse_args()
    try:
        request, _ = read_json(args.request.absolute())
        contract.validate(request, args.action)
        protected = contract.roots(request, args.action)
        contract.output(args.work_root, args.run_id, protected)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('输入无效：' + text(str(error)), file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps({'status': 'not_run', 'operation': args.action, 'summary': '只检查形状，尚未核实资料、支持度或运行结果'}, ensure_ascii=False))
        return 0
    previous = {s: signal.signal(s, cancelled) for s in [signal.SIGINT, signal.SIGTERM]}
    try:
        code, result = run(request, args.work_root, args.run_id, args.action)
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        print('执行或证据保存失败，不能报告成功：' + text(str(error)), file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():signal.signal(sig, handler)
