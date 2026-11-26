"""在相同数据范围内按子集和真实题号核对逐题原生评分。"""
from collections import defaultdict
from decimal import Decimal


def numeric(value):
    if type(value) not in (int, float, Decimal):
        raise ValueError("评分必须是有限数字")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("评分必须是有限数字")
    return result


def sample_score(value, kind):
    if kind == "binary":
        if type(value) is bool:
            return int(value)
        if numeric(value) in (0, 1):
            return int(value)
        raise ValueError("二元评分必须为 0 或 1")
    if kind != "numeric":
        raise ValueError("未知逐题计分类型")
    return numeric(value)


def key(row, parent):
    subset = row.get("subset", parent)
    if parent is not None and subset != parent:
        raise ValueError("逐题子集与指标作用范围冲突")
    if subset is not None and (not isinstance(subset, str) or not subset):
        raise ValueError("无效逐题子集")
    sample_id = row.get("sample_id")
    if type(sample_id) not in (str, int) or sample_id == "":
        raise ValueError("缺少有效题号")
    return subset, sample_id


def sample_index(rows, parent):
    if not isinstance(rows, list):
        raise ValueError("逐题结果必须为数组")
    indexed = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("逐题记录必须为对象")
        indexed[key(row, parent)].append(row)
    return indexed


def lookup(rows, parent):
    by_key = sample_index(rows, parent)
    by_id = defaultdict(list)
    for identity, group in by_key.items():
        by_id[identity[1]].extend((identity, row) for row in group)
    return by_key, by_id


def locate(index, identity):
    by_key, by_id = index
    subset, sample_id = identity
    if subset is not None:
        if len(by_key.get(identity, [])) != 1:
            raise ValueError("题号在指定子集内缺失或重复")
        return identity, by_key[identity][0]
    matches = by_id.get(sample_id, [])
    if len(matches) != 1:
        raise ValueError("题号缺失或跨子集不唯一，需指定子集")
    return matches[0]


def paired(identity, declaration, npu_index, kind):
    matched_key, measured = locate(npu_index, identity)
    gpu = sample_score(declaration.get("score"), kind)
    npu = sample_score(measured.get("score"), kind)
    row = {"subset": matched_key[0], "sample_id": matched_key[1], "gpu_score": gpu, "npu_score": npu,
           "npu_minus_gpu": npu - gpu}
    if kind == "binary":
        row["change"] = {(1, 0): "correct_to_wrong", (0, 1): "wrong_to_correct"}.get((gpu, npu), "unchanged")
    return row


def compare_samples(declared, npu):
    parent = declared.get("subset")
    entries = sample_index(declared["sample_results"], parent)
    npu_index = lookup(npu.get("samples", []), parent)
    rows = []
    unmatched = []
    for identity, group in entries.items():
        try:
            if len(group) != 1:
                raise ValueError("客户逐题基线含重复记录")
            rows.append(paired(identity, group[0], npu_index, npu["sample_kind"]))
        except ValueError as error:
            unmatched.append({"subset": identity[0], "sample_id": identity[1], "reason": str(error)})
    matched = reject_collisions(rows, unmatched, parent)
    expected = len(declared["sample_results"])
    outcome = {"status": ("not_evaluated" if not matched else "partial" if unmatched else "completed"),
               "expected": expected, "matched": len(matched), "unmatched_count": expected - len(matched),
               "items": matched, "unmatched": unmatched}
    if not expected:
        outcome["reason"] = "逐题基线为空"
    return outcome


def reject_collisions(rows, unmatched, parent):
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row, parent)].append(row)
    matched = []
    for identity, group in grouped.items():
        if len(group) == 1:
            matched.extend(group)
        else:
            unmatched.extend({"subset": identity[0], "sample_id": identity[1], "reason": "重复指向同一原始题目"}
                             for _ in group)
    return matched
