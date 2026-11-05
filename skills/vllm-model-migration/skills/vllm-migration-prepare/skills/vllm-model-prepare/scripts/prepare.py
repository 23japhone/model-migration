#!/usr/bin/env python3
"""在资产所在主机准备模型文件，不启动模型服务。"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from model_prepare.assets import inspect_assets
from model_prepare.request import normalize_request
from model_prepare.results import begin_attempt, safe_text, validate_output, write_json
from model_prepare.sources import prepare_remote


class ChineseArgumentParser(argparse.ArgumentParser):
    """将命令行帮助和用法标题显示为中文，保留参数标识。"""

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1).replace("options:\n", "参数：\n", 1)

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)


def execute(request, target, run_id):
    attempt, manifest = begin_attempt(target, request, run_id)
    destination = attempt / "model-manifest.json"
    write_json(destination, manifest)

    def save_snapshot(value):
        snapshot = attempt / "source-snapshot.json"
        write_json(snapshot, value)
        manifest["evidence"]["source_snapshot"] = str(snapshot)

    code = 0
    try:
        source = None
        with redirect_stdout(sys.stderr):
            if request["source"] != "local":
                source = prepare_remote(request, on_snapshot=save_snapshot)
            manifest["asset"] = inspect_assets(request, source)
        manifest["asset"]["operation"] = (source or {}).get("operation", "reused")
        manifest["status"] = "completed"
        manifest["conclusions"]["assets"] = "pass"
        if request["scope"] == "metadata":
            manifest["uncovered"].append("metadata 范围未准备或核验完整模型权重及 tokenizer")
        if source is None:
            manifest["uncovered"].append("本地资产来源未与上游仓库核对")
        elif source.get("file_revision_available") is False:
            manifest["uncovered"].append("SDK 未提供逐文件 revision，已通过文件大小和校验摘要锁定来源内容")
        if manifest["asset"]["weights"]["binary_structure"] == "not_evaluated" and request["scope"] == "full":
            manifest["uncovered"].append("未解码或加载非 Safetensors 二进制内容")
    except ImportError as error:
        code = 3
        manifest.update(status="blocked", diagnostic={"type": type(error).__name__, "message": f"依赖导入失败：{safe_text(error)}"})
    except (Exception, KeyboardInterrupt) as error:
        code = 1
        manifest.update(status="failed", diagnostic={"type": type(error).__name__, "message": f"准备执行失败：{safe_text(error)}"})
        manifest["conclusions"]["assets"] = "fail"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(destination, manifest)
    return code, {"manifest": str(destination), "status": manifest["status"], "conclusions": manifest["conclusions"]}


def main(argv=None):
    parser = ChineseArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("--request", required=True, type=Path, help="模型资产请求 JSON 文件，不能包含凭据")
    parser.add_argument("--work-root", required=True, type=Path, help="证据根目录，必须位于模型和已安装的技能资源之外")
    parser.add_argument("--run-id", required=True, help="各准备任务共用的运行标识")
    parser.add_argument("--plan", action="store_true", help="核验输入并输出计划，不联网或写入文件")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.request.read_text(encoding="utf-8-sig"))
        request = normalize_request(value, base=args.work_root.expanduser().resolve())
        target = validate_output(request, args.work_root, args.run_id)
    except (ValueError, OSError) as error:
        print(f"准备输入无效：{safe_text(error)}", file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps({"status": "not_run", "request": request, "evidence_parent": str(target),
                          "network": "not_used", "weights": request["scope"]}, ensure_ascii=False))
        return 0
    try:
        code, summary = execute(request, target, args.run_id)
    except OSError as error:
        print(f"无法保存准备证据：{safe_text(error)}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
