#!/usr/bin/env python3
"""只读核对 vLLM-Ascend 模型支持度，输出包外静态路线和证据。"""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from model_precheck.evidence import safe_text, sanitized
from model_precheck.request import load_request, output_target
from model_precheck.runner import run


class ChineseParser(argparse.ArgumentParser):
    """提供中文参数帮助和诊断。"""
    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:\n', '参数：', 1)

    def error(self, message):
        self.exit(2, '预检参数无效，请使用 --help 核对参数。\n')


def main():
    parser = ChineseParser(description=__doc__, add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--request', required=True, type=Path, help='本地 JSON 请求路径')
    parser.add_argument('--work-root', required=True, type=Path, help='用户指定包外证据根')
    parser.add_argument('--run-id', required=True, help='本次运行标识')
    parser.add_argument('--plan', action='store_true', help='仅核对输入，不读取模型/源码内容或写运行目录')
    args = parser.parse_args()
    try:
        request = load_request(args.request, args.work_root.expanduser().resolve())
        target = output_target(request, args.work_root, args.run_id)
    except (OSError, ValueError, TypeError) as error:
        print('预检输入无效：' + safe_text(error), file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps({'status': 'not_run', 'request': sanitized(request)}, ensure_ascii=False))
        return 0
    try:
        code, output = run(request, target, args.run_id)
    except (OSError, ValueError, KeyboardInterrupt) as error:
        print('预检证据未完整保存，不能宣告完成：' + safe_text(error), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
