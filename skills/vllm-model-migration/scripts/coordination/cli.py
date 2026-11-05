"""总入口 CLI：顶层输入、静态计划和运行委托。"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

from .planning import execution_reason, plan
from .request import output, validate


class Parser(argparse.ArgumentParser):
    """参数错误不回显可能含秘密的 argv。"""

    def error(self, message):
        self.exit(2, '参数无效，请使用 --help 核对总入口接口。\n')


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('JSON 不允许重复键')
        value[key] = item
    return value


def finite_constant(value):
    raise ValueError('JSON 不允许非有限数字')


def read_request(path):
    # 非阻塞打开避免 FIFO 等特殊文件在输入校验前永久等待。
    def nonblocking(name, flags):
        return os.open(name, flags | getattr(os, 'O_NONBLOCK', 0))

    with open(path, encoding='utf-8', opener=nonblocking) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('顶层请求须为普通文件')
        value = json.loads(stream.read(), object_pairs_hook=unique_object, parse_constant=finite_constant)
    # JSON 可合法转义孤立代理字符，但它不能形成 UTF-8 输出或文件路径。
    json.dumps(value, ensure_ascii=False).encode('utf-8')
    return value


def arguments(argv):
    parser = Parser(description='vLLM 迁移总入口：静态计划、完整链路、明确续接及服务收尾')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--request', type=Path, help='包外顶层内部请求 JSON')
    inputs.add_argument('--task', type=Path, help='客户 task.json；意图与环境由 Agent 补齐')
    parser.add_argument('--context', type=Path, help='Agent 生成的冻结观察和决策；不是客户输入')
    parser.add_argument('--resume', type=Path, help='客户流程上一次回执；只续接未完成步骤')
    parser.add_argument('--work-root', type=Path, required=True, help='包外证据根；计划不会创建目录')
    parser.add_argument('--run-id', required=True, help='本次运行标识')
    parser.add_argument('--action', choices=('run', 'analyze'), default='run', help='计划描述的操作')
    parser.add_argument('--plan', action='store_true', help='只读取顶层请求，输出路由和缺口')
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    if args.task is not None:
        from customer_workflow.cli import main as customer_main
        return customer_main(args)
    if args.context is not None or args.resume is not None:
        print('--context/--resume 仅用于客户 --task。', file=sys.stderr)
        return 2
    try:
        request = validate(read_request(args.request.expanduser().absolute()), args.action)
        parent = output(args.work_root, args.run_id)
        value = plan(request, args.action, args.run_id, parent)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError):
        # JSON 错误和 OS 异常可能包含输入片段或路径；本切片不回显原文。
        print('总入口输入无效：请核对 UTF-8 JSON、契约字段、引用格式和输出边界。', file=sys.stderr)
        return 2
    if not args.plan:
        from .runtime.runner import run
        from .runtime.sources import OutputError, ref
        reason = execution_reason(request)
        if reason or not value['ready']:
            value.update(status='blocked', next_action=reason or 'provide_missing_inputs')
        else:
            try:
                code, result = run(request, args.work_root, parent, args.run_id, args.action,
                                   ref(args.request.expanduser().absolute()))
                print(json.dumps(result, ensure_ascii=False))
                return code
            except OutputError:
                print('总入口输出与来源或资产边界冲突，未执行后续阶段。', file=sys.stderr)
                return 2
            except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
                print('总入口执行或证据失败，不能报告完成；请核对来源和输出边界。', file=sys.stderr)
                return 1
    print(json.dumps(value, ensure_ascii=False))
    return 0 if args.plan else 3
