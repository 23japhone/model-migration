"""三步定界公开 CLI；--plan 只检查形状与输出边界，不访问服务、不写运行目录。"""
import argparse
import json
from pathlib import Path
import sys

from . import runner
from .contract import output_parent, validate
from .evidence import load, record


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '参数无效，请使用 --help 核对定界接口。\n')


def parser():
    value = Parser(description='服务链路三步定界(three-step-demarcation skill)')
    value.add_argument('--request', type=Path, help='包外请求 JSON')
    value.add_argument('--work-root', type=Path, help='包外证据根')
    value.add_argument('--run-id', help='运行标识')
    value.add_argument('--action', choices=('run', 'analyze'), default='run')
    value.add_argument('--plan', action='store_true', help='只检查形状与输出边界')
    return value


def blocked(message, args):
    value = record({}, args.run_id or 'unassigned', args.action, 'blocked')
    value.update(summary=message, next_action='provide_demarcation_request')
    print(json.dumps(value, ensure_ascii=False))
    return 3


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.request:
        return blocked('定界需要 --request 引用服务与坏样本', args)
    if not args.work_root or not args.run_id:
        return blocked('请提供 --work-root 和 --run-id', args)
    try:
        request = validate(load(args.request), args.action)
        parent = output_parent(args.work_root, args.run_id)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print('定界请求无效：' + str(error), file=sys.stderr)
        return 2
    if args.plan:
        planned = record(request, args.run_id, args.action, 'not_run')
        planned['evidence_parent'] = str(parent)
        print(json.dumps(planned, ensure_ascii=False))
        return 0
    try:
        operation = {'run': runner.run, 'analyze': runner.analyze}[args.action]
        code, result = operation(request, parent, args.run_id)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print('定界执行或证据保存失败：' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return code
