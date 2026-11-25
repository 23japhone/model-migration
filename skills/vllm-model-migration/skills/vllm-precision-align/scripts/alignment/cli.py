"""阶段CLI及纯计划；不依赖维护后端。"""
import argparse
import json
from pathlib import Path
import signal
import sys

from .evidence import EvidenceError,read,safe
from .request import output,validate


class Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'参数无效，请使用--help核对阶段接口。\n')


def cancelled(signum,frame):raise KeyboardInterrupt('收到阶段取消信号')


def main(argv=None):
    parser=Parser(description='编排准备、部署及 EvalScope/AISBench 精度测试，或只读分析历史产物')
    parser.add_argument('--request',type=Path,required=True,help='包外阶段请求JSON')
    parser.add_argument('--work-root',type=Path,required=True,help='独立证据根')
    parser.add_argument('--run-id',required=True,help='本次运行标识')
    parser.add_argument('--action',choices=('run','analyze','quality'),default='run')
    parser.add_argument('--plan',action='store_true',help='只检查形状和安装资源输出边界')
    args=parser.parse_args(argv)
    try:
        raw = read(args.request.expanduser().absolute())
        if args.action == 'quality':
            from quality_stage.runner import validate as quality_request
            request = quality_request(raw)
        else:
            request = validate(raw, args.action)
        parent = output(args.work_root, args.run_id)
    except (OSError,ValueError,KeyError,TypeError,RecursionError) as error:
        print('阶段输入无效：'+safe(error),file=sys.stderr);return 2
    if args.plan:
        print(json.dumps({'schema_version':'1.0','task_id':'vllm-precision-align','run_id':args.run_id,
            'status':'not_run','operation':args.action,'inputs':request,'evidence_parent':str(parent)},ensure_ascii=False));return 0
    previous={sig:signal.signal(sig,cancelled) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        if args.action == 'quality':
            from quality_stage.runner import run
        else:
            from .runner import run
        code,value=run(request,args.work_root,args.run_id,args.action)
    except EvidenceError as error:
        print('阶段证据不完整：'+safe(error),file=sys.stderr);return 1
    except (OSError,ValueError,KeyError,TypeError,RecursionError,KeyboardInterrupt) as error:
        print('阶段执行未开始或未完整交付：'+safe(error),file=sys.stderr)
        return 1 if isinstance(error,KeyboardInterrupt) else 2
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
    print(json.dumps(value,ensure_ascii=False));return code
