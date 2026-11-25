"""按真实生成顺序核验公共前文上的全词表预测，交付首个异常。"""
import re

from .evidence import bound, path_of
from .samples import numeric
from .token_cpu import validate_spec
from .token_reference import verify_cpu
from .token_vectors import peak_error, read_step

ROW_ERRORS = (ValueError, TypeError, KeyError, OSError, IndexError, ArithmeticError)


def token_request(req):
    required = {"gpu", "npu", "token_alignment"}
    if not required <= req.keys() or req.keys() - required - {"cpu", "cpu_model", "case"}:
        raise ValueError("Token 比较请求字段无效")
    if "case" in req:
        case = req["case"]
        if not isinstance(case, dict) or set(case) != {"subset", "sample_id"}:
            raise ValueError("case 须绑定重跑清单中的子集和题号")
        sample_key(case)
    if "cpu_model" in req:
        if "cpu" in req:
            raise ValueError("cpu 与 cpu_model 只选一种")
        validate_spec(req["cpu_model"])
    for role in ("gpu", "npu", "cpu"):
        if role in req:
            ref = req[role]
            if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
                raise ValueError("Token 记录须提供 path 和 sha256")
            path_of(ref["path"])
            if not isinstance(ref["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]):
                raise ValueError("非法的 SHA256 摘要")
    return tolerances(req["token_alignment"])


def tolerances(policy):
    if not isinstance(policy, dict) or set(policy) != {"raw_logits", "processed_scores"}:
        raise ValueError("须分别提供 raw_logits 和 processed_scores 的绝对容差")
    limits = {}
    for key, spec in policy.items():
        if not isinstance(spec, dict) or set(spec) != {"atol"}:
            raise ValueError("每类预测只接受 atol 绝对容差")
        limits[key] = numeric(spec["atol"])
        if limits[key] < 0:
            raise ValueError("绝对容差不得为负数")
    return limits


def outcome(result, reason, covered, first=None, **fields):
    return {"result": result, "reason": reason, "first_anomalous_token": first,
            "covered_tokens": covered, **fields}


def missing(reason, covered, first=None):
    return outcome("insufficient_evidence", reason, covered, first_missing_token=first)


def sample_key(row):
    if type(row.get("sample_id")) not in (int, str) or row["sample_id"] == "":
        raise ValueError("缺少有效题号")
    if "subset" in row and (not isinstance(row["subset"], str) or not row["subset"]):
        raise ValueError("子集无效")
    return row.get("subset"), row["sample_id"]


def record_identity(row, case):
    subset, sample = sample_key(row)
    if case and (sample != case["sample_id"] or subset not in (None, case["subset"])):
        raise ValueError("样本身份与重跑清单的题号或子集不符")
    prompt = row.get("prompt_token_ids")
    if not isinstance(prompt, list) or not prompt or any(type(i) is not int or i < 0 for i in prompt):
        raise ValueError("缺少有效输入 Token 序列")
    if not isinstance(row.get("output_token_ids"), list):
        raise ValueError("缺少实际接受的输出 Token 序列")
    return case["subset"] if case else subset, sample


def step(artifact, index, expected_size):
    raw, probs = read_step(artifact, index)
    token = artifact.value["output_token_ids"][index]
    if not index and max(artifact.value["prompt_token_ids"]) >= len(raw):
        raise ValueError("有效输入的 Token ID 超出所给词表")
    if expected_size is not None and len(raw) != expected_size:
        raise ValueError("词表长度在生成期间改变")
    if type(token) is not int or not 0 <= token < len(raw):
        raise ValueError("实际接受的 Token ID 超出词表或无效")
    return raw, probs, token


def discrepancy(left, right, limits):
    differences = {}
    for key, values, other in zip(("raw_logits", "processed_scores"), left, right):
        candidate, delta = peak_error(values, other)
        if delta > limits[key]:
            differences[key] = {"token_id": candidate, "gpu": values[candidate], "npu": other[candidate],
                                "max_abs_error": delta, "atol": limits[key]}
    if left[2] != right[2]:
        differences["selected_token"] = {"gpu": left[2], "npu": right[2]}
    return differences


def early_stop(left, right, lengths, covered, vocab_size):
    shorter = "gpu" if lengths[0] < lengths[1] else "npu"
    extra = (right if shorter == "gpu" else left)["output_token_ids"][covered]
    if type(extra) is not int or extra < 0 or (vocab_size is not None and extra >= vocab_size):
        return missing("较长序列的首个单端位置没有有效接受 Token", covered, covered + 1)
    return outcome("early_stop", "一端已结束，下一位置只有单端输出", covered, covered + 1,
                   stopped_earlier=shorter, stop_reasons={"gpu": left["stop_reason"], "npu": right["stop_reason"]})


def finish(left, right, lengths, vocab_size):
    covered = min(lengths)
    if not all(isinstance(row.get("stop_reason"), str) and row["stop_reason"] for row in (left, right)):
        return missing("缺少实际停止原因", covered)
    stops = {"gpu": left["stop_reason"], "npu": right["stop_reason"]}
    if lengths[0] != lengths[1]:
        return early_stop(left, right, lengths, covered, vocab_size)
    if {"stop", "unknown_stop"}.intersection(stops.values()) and "length" not in stops.values():
        return missing("仅有粗粒度停止标记，无法确认两端实际停止原因", covered)
    if left["stop_reason"] != right["stop_reason"]:
        return outcome("stop_reason_mismatch", "输出序列相同，实际停止原因不同", covered,
                       stop_after_tokens=covered, stop_reasons=stops)
    if not covered:
        return missing("双方均无可核验的实际输出", 0)
    return outcome("aligned", "所有输出位置的分数、接受 Token 和停止原因一致", covered)


def compare_records(gpu, npu, limits, case):
    left, right = gpu.value, npu.value
    try:
        if record_identity(left, case) != record_identity(right, case):
            return missing("GPU 与 NPU 的题号或子集不一致", 0)
    except ROW_ERRORS as exc:
        return missing(str(exc), 0)
    if left["prompt_token_ids"] != right["prompt_token_ids"]:
        a, b = left["prompt_token_ids"], right["prompt_token_ids"]
        differing = next((i for i, pair in enumerate(zip(a, b), 1) if pair[0] != pair[1]), min(len(a), len(b)) + 1)
        return outcome("input_mismatch", "两端实际输入 Token 序列不同", 0, first_different_input_token=differing)
    lengths = len(left["output_token_ids"]), len(right["output_token_ids"])
    vocab_size = None
    for index in range(min(lengths)):
        try:
            a = step(gpu, index, vocab_size)
            b = step(npu, index, len(a[0]))
            vocab_size = len(a[0])
            differences = discrepancy(a, b, limits)
        except ROW_ERRORS as exc:
            return missing(str(exc), index, index + 1)
        if differences:
            return outcome("mismatch", "、".join(differences), index + 1, index + 1,
                           gpu_token_id=a[2], npu_token_id=b[2], differences=differences)
    return finish(left, right, lengths, vocab_size)


def run(req, request_ref):
    limits = tolerances(req["token_alignment"])
    devices = {role: bound(req[role]) for role in ("gpu", "npu")}
    case = req.get("case")
    report = compare_records(devices["gpu"], devices["npu"], limits, case)
    sample = devices["gpu"].value
    places = {role: source.ref for role, source in devices.items()}
    kinds = {role: source.value.get("evidence_kind", "unspecified") for role, source in devices.items()}
    incomplete = report["result"] in ("insufficient_evidence", "input_mismatch")
    report.update(schema_version="1.0", task_id="vllm-precision-compare", operation="token",
                  status="completed", sample_id=sample.get("sample_id"), subset=(case or sample).get("subset"),
                  validity="not_evaluated" if incomplete else "valid",
                  evidence={"request": request_ref, **places}, evidence_kind=kinds)
    report["cpu_check"] = verify_cpu(req, devices, report, limits)
    return report
