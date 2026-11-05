#!/usr/bin/env python3
"""为 vLLM-Ascend 后续评测准备原始数据与可复查的选择清单。"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from dataset_prepare.native_layout import prepare_asset
from dataset_prepare.request import normalize_request
from dataset_prepare.results import begin_attempt,output_parent,safe_text,write_json
from dataset_prepare.resolution import prepare_source, verify_resolution


class ChineseArgumentParser(argparse.ArgumentParser):
    """显示中文帮助标题，保留统一参数名称。"""
    def format_help(self):return super().format_help().replace('usage: ','用法：',1).replace('options:\n','参数：\n',1)
    def format_usage(self):return super().format_usage().replace('usage: ','用法：',1)


def execute(request,parent,run_id):
    attempt,manifest=begin_attempt(parent,request,run_id);destination=attempt/'dataset-manifest.json';write_json(destination,manifest)
    def snapshot(value):write_json(attempt/'source-snapshot.json',value)
    code=0
    try:
        path,source,operation=prepare_source(request,snapshot);snapshot(source)
        asset=prepare_asset(request,path,attempt);asset.update(source=source,operation=operation)
        verify_resolution(source,asset)
        manifest.update(asset=asset,status='completed');manifest['conclusions']['assets']='pass'
        write_json(attempt/'sample-selection.json',asset['selection'])
        if source['kind']=='local':manifest['uncovered'].append('本地数据未与上游来源核对')
        else:manifest['uncovered'].append('HTTPS 来源未提供已核对的不可变上游版本；SHA256 固定本次内容，不将 branch 或 ETag 当作 repo commit')
        manifest['uncovered'].append('选择清单未自动接入任何评测器，后续调用方必须核对实际 split 与样本范围')
    except (Exception,KeyboardInterrupt) as error:
        code=1;manifest.update(status='failed',diagnostic={'type':type(error).__name__,'message':'数据集准备失败：'+safe_text(error)});manifest['conclusions']['assets']='fail'
    manifest['finished_at']=datetime.now(timezone.utc).isoformat();write_json(destination,manifest)
    return code,{'manifest':str(destination),'status':manifest['status'],'conclusions':manifest['conclusions']}


def main(argv=None):
    parser=ChineseArgumentParser(description=__doc__,add_help=False)
    parser.add_argument('-h','--help',action='help',help='显示帮助并退出')
    parser.add_argument('--request',required=True,type=Path,help='数据集请求 JSON 文件，不能包含凭据')
    parser.add_argument('--work-root',required=True,type=Path,help='位于包外与数据资产外的证据根目录')
    parser.add_argument('--run-id',required=True,help='准备任务共用的运行标识')
    parser.add_argument('--plan',action='store_true',help='仅核验输入与路径，不联网、解包或写运行文件')
    args=parser.parse_args(argv)
    try:
        request=normalize_request(json.loads(args.request.read_text(encoding='utf-8-sig')),args.work_root.expanduser().resolve());parent=output_parent(request,args.work_root,args.run_id)
    except (ValueError,OSError) as error:print('数据集输入无效：'+safe_text(error),file=sys.stderr);return 2
    if args.plan:
        print(json.dumps({'status':'not_run','request':request,'evidence_parent':str(parent),'network':'not_used'},ensure_ascii=False));return 0
    try:code,result=execute(request,parent,args.run_id)
    except OSError as error:print('无法保存数据集证据：'+safe_text(error),file=sys.stderr);return 1
    print(json.dumps(result,ensure_ascii=False));return code


if __name__=='__main__':raise SystemExit(main())
