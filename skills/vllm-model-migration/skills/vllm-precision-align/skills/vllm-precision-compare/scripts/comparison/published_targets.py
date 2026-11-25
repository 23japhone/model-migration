"""从同轮 GPU 原始运行记录中核对发布者给出的允许下降量。"""
from collections import defaultdict

from .evidence import bound
from .samples import numeric


def original(index, receipt):
    ref = index.get("published_source")
    if not ref:
        return receipt
    # 搜索器重定位原始文件路径；业务字段必须与发布者的原始字节完全一致。
    published = bound(ref)
    changed = dict(receipt.value, files=published.value["files"])
    if changed != published.value:
        raise ValueError("公开目标与原始 GPU 运行记录不一致")
    digests = published.value["files"].values()
    if any(index[key]["sha256"] not in digests for key in ("config", "report")):
        raise ValueError("公开目标未绑定本次 GPU 配置和评分报告")
    return published


def target(row, native, source):
    if row.get("dataset", source["protocol"]["dataset"]) != source["protocol"]["dataset"]:
        raise ValueError("数据集不同")
    if row.get("run_id", source["run_id"]) != source["run_id"]:
        raise ValueError("GPU 运行 ID 不同")
    if numeric(row["gpu_score"]) != numeric(native["value"]):
        raise ValueError("GPU 分值与原始报告不同")
    declared = row["target"]
    if not isinstance(declared, dict) or set(declared) != {"max_drop"} or numeric(declared["max_drop"]) < 0:
        raise ValueError("max_drop 须为显式的非负有限数值")
    return declared


def annotate(index, receipt, scores):
    published = original(index, receipt)
    rows = published.value.get("scores")
    if not isinstance(rows, list):
        return
    candidates = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("metric"), str) and "target" in row:
            subset = row.get("subset")
            if subset is None or isinstance(subset, str):
                candidates[row["metric"], subset].append(row)
    for native in scores:
        matches = candidates[native["metric"], native["subset"]]
        if len(matches) != 1 or "value" not in native:
            continue
        try:
            value = target(matches[0], native, published.value)
        except (TypeError, ValueError, KeyError):
            continue  # 目标依据不足，经过核验的原生分数仍可计算分差。
        native["published_target"] = {"kind":"publisher", "source":published.ref, "target":value}
