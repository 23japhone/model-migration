"""中文参数、无副作用计划及明确取消边界。"""
import argparse
import json
from pathlib import Path
import signal
import sys

from .evidence import read,safe
from .request import output,validate
from .runner import run,finalize


class ChineseParser(argparse.ArgumentParser):
    def format_help(self):return super().format_help().replace('usage: ','用法：',1).replace('options:\n','参数：\n',1)
    def error(self,message):self.exit(2,'参数无效，请使用 --help 核对输入。\n')


def cancelled(signum,frame):raise KeyboardInterrupt('收到取消信号 '+str(signum))


def main():
    parser = ChineseParser(description='按预检路线编排模型迁移，汇总真实验证及资源交接',add_help=False)
    parser.add_argument('-h','--help',action='help',help='显示帮助')
    parser.add_argument('--request',type=Path,required=True,help='阶段JSON请求绝对路径')
    parser.add_argument('--work-root',type=Path,required=True,help='包外运行目录')
    parser.add_argument('--run-id',required=True,help='本次运行标识')
    parser.add_argument('--action',choices=['run','finalize'],default='run',help='执行阶段或只读收敛语义复核')
    parser.add_argument('--plan',action='store_true',help='仅检查顶层形状和输出保护，不读引用资料或写目录')
    args = parser.parse_args()
    try:
        request = validate(read(args.request.absolute()),args.action);output(args.work_root,args.run_id)
    except (OSError,ValueError,TypeError,KeyError) as error:
        print('阶段输入无效：'+safe(error),file=sys.stderr);return 2
    if args.plan:
        print(json.dumps({'status':'not_run','action':args.action,'summary':'仅检查阶段请求形状；资产、路线和运行尚未核实'},ensure_ascii=False));return 0
    previous = {s:signal.signal(s,cancelled) for s in [signal.SIGINT,signal.SIGTERM]}
    try:
        code,result = (run if args.action=='run' else finalize)(request,args.work_root,args.run_id)
        print(json.dumps(result,ensure_ascii=False));return code
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,KeyboardInterrupt) as error:
        print('阶段执行或证据失败，不能报告完成：'+safe(error),file=sys.stderr);return 1
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
