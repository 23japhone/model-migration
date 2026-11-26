"""按数据集、原生指标及可选子集判定任务分数。"""
from decimal import Decimal

from .samples import compare_samples, numeric


# 这些原生指标的定义具有可比较的比例尺度。其他自定义指标可显式声明。
RATIO_METRICS = {"mae", "mean_absolute_error", "mse", "mean_squared_error", "rmse",
                 "root_mean_squared_error", "ppl", "perplexity"}


def find_score(measured, declared):
    candidates = [row for row in measured["scores"] if isinstance(row, dict)
                  and row.get("subset") == declared.get("subset") and row.get("metric") == declared["metric"]]
    if len(candidates) != 1:
        raise ValueError("该作用范围的原生指标缺失或不唯一")
    if "error" in candidates[0]:
        raise ValueError(candidates[0]["error"])
    return candidates[0]


def check_scope(baseline, declared, protocol):
    scopes = set(protocol.get("subsets", []))
    subset = declared.get("subset")
    if subset is not None:
        if scopes and subset not in scopes:
            raise ValueError("NPU 测量缺少子集：" + subset)
    elif "subset_list" in baseline and set(baseline["subset_list"]) != scopes:
        raise ValueError(f"NPU 总分范围不同，所需子集 {baseline['subset_list']}，实际子集 {sorted(scopes)}")


def native_metric(measured, declared):
    row = find_score(measured, declared)
    spec = measured["protocol"]["metrics"][declared["metric"]]
    if row.get("direction") not in ("higher", "lower") or not row.get("unit"):
        raise ValueError("NPU 原生指标缺少单位或方向")
    if any(spec.get(key) != row[key] for key in ("unit", "direction")):
        raise ValueError("NPU 成绩与原生指标定义不同")
    if not row.get("aggregation") or row["aggregation"] != spec["selector"]["aggregation"]:
        raise ValueError("NPU 成绩与原生指标聚合方式不同")
    return row, spec


def publisher_scope(historical, baseline, declared, measured):
    protocol = historical["protocol"]
    if protocol["dataset"] != baseline["dataset"]:
        raise ValueError("公开 GPU 数据集不同")
    if declared.get("subset") is None:
        scopes = baseline.get("subset_list", measured["protocol"]["subsets"])
        if set(protocol["subsets"]) != set(scopes):
            raise ValueError("公开 GPU 总分与所声明的子集范围不同")


def published_score(historical, baseline, declared, measured, current, required):
    if not historical:
        if required:
            raise ValueError("未核实该数据集的公开 GPU 分数")
        return {}
    try:
        publisher_scope(historical, baseline, declared, measured)
        found = find_score(historical, declared)
        name = declared["metric"]
        old = historical["protocol"]["metrics"][name]
        if old["selector"] != measured["protocol"]["metrics"][name]["selector"]:
            raise ValueError("公开 GPU 原生指标选择器与当前指标不同")
        if any(found.get(k) != current[k] for k in ("unit", "direction", "aggregation")):
            raise ValueError("公开 GPU 指标的单位、方向或聚合与所选指标不同")
        if required and numeric(found["value"]) != numeric(declared["gpu_score"]):
            raise ValueError("公开 GPU 分数与已固定的发布记录不同")
        return found
    except (KeyError, ValueError, TypeError):
        if required:
            raise
        return {}


def threshold(declared, public, gpu, spec, basis):
    explicit = declared.get("target") or {}
    if "max_drop" in explicit and basis == "customer_provided":
        return numeric(explicit["max_drop"]), "customer"
    if public:
        return numeric(public["target"]["max_drop"]), "publisher"
    scale = spec.get("scale_type")
    if scale is None:
        native = spec["selector"]["name"].casefold()
        scale = "ratio" if spec["unit"] in ("ratio", "percent") or native in RATIO_METRICS else "interval"
    if scale == "ratio" and gpu >= 0:
        return gpu * Decimal("0.01"), "default_relative"
    return Decimal(0), "default_zero"


def target(row, declared, public, spec, basis):
    gpu, npu = numeric(row["gpu_score"]), row["npu_score"]
    limit, source = threshold(declared, public, gpu, spec, basis)
    if limit < 0:
        raise ValueError("允许下降量须非负")
    delta = npu - gpu
    drop = -delta if row["direction"] == "higher" else delta
    row.update(comparability="pass", npu_minus_gpu=delta, drop=drop, target={"max_drop": limit},
               target_source=source, precision_target="pass" if drop <= limit else "fail")
    if source == "publisher":
        row["target_evidence"] = public


def score(baseline, declared, measured, historical, basis):
    row = {"subset": declared.get("subset"), "metric": declared["metric"], "gpu_score": declared["gpu_score"],
           "npu_score": None, "comparability": "review_required", "precision_target": "not_evaluated", "reasons": []}
    try:
        if measured["protocol"].get("dataset") != baseline["dataset"]:
            raise ValueError("NPU 测量没有对应的数据集")
        check_scope(baseline, declared, measured["protocol"])
        npu, spec = native_metric(measured, declared)
        row.update(npu_score=numeric(npu["value"]), unit=npu["unit"], direction=npu["direction"],
                   aggregation=npu["aggregation"])
        if row["gpu_score"] is None:
            raise ValueError("公开 GPU 来源缺少该范围的有效分数")
        published = published_score(historical, baseline, declared, measured, npu, basis == "publisher")
        target(row, declared, published.get("published_target"), spec, basis)
        if "sample_results" in declared:
            row["sample_comparison"] = compare_samples(declared, npu)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        row["reasons"].append(str(error))
    return row


def verdict(rows):
    outcomes = {row["precision_target"] for row in rows}
    if "fail" in outcomes:
        return "fail"
    return "pass" if outcomes == {"pass"} else "not_evaluated"


def compare(req, measured, historical, refs, issues):
    baseline = req["baseline"]
    basis = req.get("baseline_source", "customer_provided")
    rows = [score(baseline, item, measured, historical, basis) for item in baseline["scores"]]
    comparable = "pass" if all(row["comparability"] == "pass" for row in rows) else "review_required"
    conclusions = {"evaluation_validity": "pass", "comparability": comparable, "precision_target": verdict(rows)}
    report = {"schema_version": "1.0", "task_id": "vllm-precision-compare", "operation": "run",
              "source": req["source"], "status": "completed", "baseline": baseline, "scores": rows,
              "comparison_basis": basis, "conclusions": conclusions, "evidence": refs, "uncovered": issues}
    if conclusions["precision_target"] == "fail":
        report["next_action"] = {"stage": "精度比较", "tasks": ["Token 预测对齐", "张量与算子对齐"],
                                 "requires_separate_run": True,
                                 "failed_scores": [{"dataset": baseline["dataset"], "subset": row["subset"],
                                                    "metric": row["metric"]} for row in rows
                                                   if row["precision_target"] == "fail"]}
    return report
