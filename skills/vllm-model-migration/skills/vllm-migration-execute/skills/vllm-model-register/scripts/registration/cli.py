"""两个公开 CLI 的中文参数和退出协议。"""
import argparse
import json
from pathlib import Path
import sys

from .evidence import safe_text, sanitized
from .request import load_request, output_target
from .runner import run


class ChineseParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:\n', '参数：', 1)

    def error(self, message):
        self.exit(2, '注册参数无效，请使用 --help 核对参数。\n')


def main(operation):
    description = '建立或复用模型注册并完成独立初检' if operation == 'register' else '只读复验实际载体内的模型注册与 class import'
    parser = ChineseParser(description=description, add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--request', required=True, type=Path, help='本地 JSON 请求')
    parser.add_argument('--work-root', required=True, type=Path, help='包外证据根')
    parser.add_argument('--run-id', required=True, help='本次运行标识')
    parser.add_argument('--plan', action='store_true', help='仅核对请求和路径，不读目标资料、安装或 import')
    args = parser.parse_args()
    try:
        request = load_request(args.request)
        target = output_target(request, args.work_root, args.run_id)
    except (OSError, ValueError, TypeError) as error:
        print('注册输入无效：' + safe_text(error), file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps({'status': 'not_run', 'operation': operation, 'request': sanitized(request)}, ensure_ascii=False))
        return 0
    try:
        code, result = run(request, target, args.run_id, operation)
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        print('注册执行或证据未完整保存，不能宣告完成：' + safe_text(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return code
