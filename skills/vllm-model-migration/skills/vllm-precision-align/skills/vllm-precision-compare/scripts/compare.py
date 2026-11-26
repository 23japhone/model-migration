#!/usr/bin/env python3
"""任务质量、Token 预测及逐调用张量比较 CLI。"""
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

sys.dont_write_bytecode = True

from comparison.evidence import gpu_evidence, npu_evidence, path_of, read
from comparison.quality import compare
from comparison.samples import numeric
from comparison import token


def json_text(value):
    # 保留原生十进制数，包括接近阈值时无法用二进制浮点精确表达的有效位。
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(json_text(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + json_text(item)
                              for key, item in value.items()) + "}"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def request(value):
    mandatory = {"baseline", "measurement", "receipt", "source"}
    if not mandatory <= value.keys() or value.keys() - mandatory - {"gpu_evidence", "verification", "baseline_source"}:
        raise ValueError("比较请求字段无效")
    for key in ("measurement", "receipt", "verification", "gpu_evidence"):
        if key in value:
            path_of(value[key])
    if value["source"] not in ("reused", "rerun"):
        raise ValueError("NPU 成绩来源无效")
    if value.get("baseline_source", "customer_provided") not in ("customer_provided", "publisher"):
        raise ValueError("GPU 分数来源无效")
    baseline = value["baseline"]
    if not isinstance(baseline, dict) or not isinstance(baseline.get("scores"), list) or not baseline["scores"]:
        raise ValueError("缺少待比较的指标")


def omitted(value):
    return value is None or type(value) is int and value == -1


def normalize_score(item, basis):
    if not isinstance(item, dict) or not isinstance(item.get("metric"), str) or not item["metric"].strip():
        raise ValueError("GPU 分数缺少指标")
    if basis != "publisher" or item["gpu_score"] is not None:
        numeric(item["gpu_score"])
    return {key: value for key, value in item.items()
            if key not in ("subset", "target", "sample_results") or not omitted(value)}


def normalize_baseline(baseline, basis):
    required = ("dataset", "evaluator")
    if any(not isinstance(baseline.get(key), str) or not baseline[key].strip() for key in required):
        raise ValueError("GPU 基线须指定 benchmark 和数据集")
    result = {key: val for key, val in baseline.items() if not omitted(val)}
    normalized = []
    scopes = set()
    for item in baseline["scores"]:
        score = normalize_score(item, basis)
        scope = score.get("subset"), score["metric"]
        if scope in scopes:
            raise ValueError("客户基线重复声明相同指标与作用范围")
        scopes.add(scope)
        normalized.append(score)
    return {**result, "scores": normalized}


def destination(args):
    if args.output:
        if args.work_root or args.run_id:
            raise ValueError("--output 与 --work-root/--run-id 不能混用")
        output = path_of(args.output)
    else:
        if not args.run_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.run_id):
            raise ValueError("须提供有效 --run-id")
        output = path_of(args.work_root) / args.run_id / "vllm-precision-compare" / uuid4().hex / "comparison.json"
    root = Path(__file__).resolve().parents[5]
    if output.resolve() != output or output.is_relative_to(root):
        raise ValueError("输出必须位于完整 Skill 资源根之外的规范路径")
    return output


def run(req, request_ref):
    measured, refs = npu_evidence(req)
    refs["request"] = request_ref
    historical = None
    issues = []
    public = req.get("baseline_source") == "publisher"
    needs_limit = any(not item.get("target") for item in req["baseline"]["scores"])
    if "gpu_evidence" in req and (public or needs_limit):
        try:
            historical, gpu_refs = gpu_evidence(req["gpu_evidence"])
            refs.update(gpu_refs)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            issues.append("GPU 历史来源不可用：" + str(error))
    elif public:
        issues.append("缺少公开 GPU 分数来源")
    return compare(req, measured, historical, refs, issues)


def publish(output, payload):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
        os.link(temporary, output)  # 原子发布且不会覆盖已有结果。
    finally:
        temporary.unlink(missing_ok=True)


def inspect_gpu(path, include_samples=False):
    evidence, refs = gpu_evidence(path)
    # 搜索资格只要数据集/分项分数，逐题原文留在已固定来源。
    rows = evidence["scores"]
    if not include_samples:
        rows = [{key: value for key, value in row.items() if key not in ("samples", "sample_errors")}
                for row in rows]
    if not any("error" not in row and row.get("value") is not None for row in rows):
        raise ValueError("原生 GPU 归档没有任何作用范围可核验的成绩")
    print(json_text({**evidence, "scores": rows, "evidence": refs}))
    return 0


def operation_for(action, value):
    if action == "tensor":
        from comparison.tensor import contract, report
        contract.request(value)
        return report.run
    if action == "token":
        token.token_request(value)
        return token.run
    request(value)
    value["baseline"] = normalize_baseline(value["baseline"], value.get("baseline_source"))
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--request")
    operation.add_argument("--inspect-gpu", help="只读核验历史 GPU 资料并列出原生成绩")
    parser.add_argument("--action", choices=("quality", "token", "tensor"))
    parser.add_argument("--include-samples", action="store_true", help="核验 GPU 归档时同时提取逐题成绩")
    parser.add_argument("--output")
    parser.add_argument("--work-root")
    parser.add_argument("--run-id")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    try:
        if args.inspect_gpu:
            if args.output or args.plan or args.work_root or args.run_id or args.action:
                raise ValueError("--inspect-gpu 只输出原始 GPU 证据摘要")
            return inspect_gpu(args.inspect_gpu, args.include_samples)
        if args.include_samples:
            raise ValueError("--include-samples 须与 --inspect-gpu 同用")
        req = read(args.request)
        compare_operation = operation_for(args.action, req.value)
        output = destination(args)
        if args.plan:
            print(json.dumps({"status": "not_run", "task_id": "vllm-precision-compare"}))
            return 0
        report = compare_operation(req.value, req.ref)
        report["report_path"] = str(output)
        payload = json_text(report) + "\n"
        publish(output, payload)
        print(payload, end="")
        return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
