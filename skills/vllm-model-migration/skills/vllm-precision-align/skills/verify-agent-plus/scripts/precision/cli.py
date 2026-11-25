"""精度任务公开 CLI；旧参数仅作为显式一致性约束。"""
import argparse
import json
import os
from pathlib import Path
import sys

from .contract import output_parent, validate
from .evidence import load, record


class Parser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'参数无效，请使用 --help 核对精度任务接口。\n')


def parser():
    value=Parser(description='EvalScope 默认数据集精度测试、AISBench 兼容与已有产物分析')
    value.add_argument('--request',type=Path,help='包外请求 JSON')
    value.add_argument('--work-root',type=Path,help='包外证据根')
    value.add_argument('--run-id',help='运行标识')
    value.add_argument('--action',choices=('run','analyze','preflight','quality-run','quality-analyze'),default='run')
    value.add_argument('--plan',action='store_true',help='只检查形状与输出边界')
    for flag in ('model-path','model-name','hardware-type','report-path','dataset','host'):
        value.add_argument('--'+flag)
    for flag in ('port','tp-size','max-model-len'):
        value.add_argument('--'+flag,type=int)
    value.add_argument('--gpu-mem-util',type=float)
    value.add_argument('--quantized',action='store_true')
    value.add_argument('--run-accuracy',action='store_true')
    value.add_argument('--skip-accuracy',action='store_true')
    value.add_argument('--perf-tests',nargs='+')
    return value


def blocked(message,args):
    value=record({},args.run_id or 'unassigned',args.action,'blocked')
    value.update(summary=message,next_action='provide_precision_request')
    print(json.dumps(value,ensure_ascii=False))
    return 3


def legacy_constraints(args,request):
    if args.skip_accuracy or args.perf_tests or args.quantized:
        raise ValueError('跳过精度、性能或部署量化请求应显式交由相应任务，本入口不执行')
    if args.report_path:raise ValueError('报告由 work-root/run-id 下的独立尝试生成，不覆盖旧 report-path')
    legacy={key:getattr(args,key) for key in ('model_path','model_name','port','dataset','host','tp_size','max_model_len','gpu_mem_util','hardware_type')
            if getattr(args,key) is not None}
    if legacy:request['_legacy_constraints']=legacy
    return request


def is_quality(action, value):
    if action.startswith('quality-'):
        return True
    return action == 'analyze' and isinstance(value, dict) and 'expected' in value


def quality_command(args, raw):
    from .quality import contract, history, run
    from .runtime.process import handling_signals
    action = 'quality-analyze' if args.action == 'analyze' else args.action
    request = contract.validate(raw, action)
    parent = output_parent(args.work_root, args.run_id)
    if args.plan:
        result = record(request, args.run_id, action, 'not_run')
        result['evidence_parent'] = str(parent)
        code = 0
    else:
        operation = run.run if action == 'quality-run' else history.analyze
        with handling_signals():
            code, result = operation(request, parent, args.run_id)
    print(json.dumps(result, ensure_ascii=False))
    return code


def main(argv=None):
    args=parser().parse_args(argv)
    if not args.request and os.environ.get('PRECISION_REQUEST'):args.request=Path(os.environ['PRECISION_REQUEST'])
    if not args.request:return blocked('旧精度入口需要完整部署交接、数据身份及评测工具配置；未启动服务或安装依赖',args)
    if not args.work_root or not args.run_id:return blocked('请提供 --work-root 和 --run-id',args)
    try:
        raw=load(args.request)
        if is_quality(args.action, raw):
            return quality_command(args, raw)
        request=validate(raw,args.action)
        if args.action!='analyze':request=legacy_constraints(args,request)
        parent=output_parent(args.work_root,args.run_id)
    except (OSError,ValueError,TypeError,KeyError) as error:
        print('精度请求无效：'+str(error),file=sys.stderr);return 2
    if args.plan:
        planned=record(request,args.run_id,args.action,'not_run');planned['evidence_parent']=str(parent)
        print(json.dumps(planned,ensure_ascii=False));return 0
    from . import runner, preflight
    try:
        operation={'run':runner.run,'analyze':runner.analyze,'preflight':preflight.run}[args.action]
        from .runtime.process import handling_signals
        with handling_signals():code,result=operation(request,parent,args.run_id)
    except (KeyboardInterrupt,SystemExit) as error:
        result=record(request,args.run_id,args.action,'failed')
        result.update(summary='输入核验期间取消',diagnostic={'type':type(error).__name__,'message':str(error)})
        code=1
    except (OSError,ValueError,TypeError,KeyError) as error:
        print('精度执行或证据保存失败：'+str(error),file=sys.stderr);return 1
    print(json.dumps(result,ensure_ascii=False));return code
