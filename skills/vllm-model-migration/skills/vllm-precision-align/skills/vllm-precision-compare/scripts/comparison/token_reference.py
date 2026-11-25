"""在首疑点核对已有 CPU 单步参考，独立记录两端误差。"""
from pathlib import Path

from .evidence import bound
from .token_cpu import forward
from .token_vectors import peak_error, read_step, step_vectors


def check_prefix(cpu, gpu, position, subset):
    row = gpu.value
    if type(cpu.get("position")) is not int or cpu["position"] != position:
        raise ValueError("CPU 位置与首疑点不符")
    if cpu.get("sample_id") != row["sample_id"] or type(cpu.get("sample_id")) is not type(row["sample_id"]):
        raise ValueError("CPU 参考题号不符")
    if "subset" in cpu and cpu["subset"] != subset:
        raise ValueError("CPU 参考子集不符")
    prefix = row["prompt_token_ids"] + row["output_token_ids"][:position - 1]
    for field, expected in (("prompt_token_ids", row["prompt_token_ids"]), ("prefix_token_ids", prefix)):
        if cpu.get(field) != expected or any(type(i) is not int for i in cpu[field]):
            raise ValueError("CPU 参考有效前文不符")


def measure(device, oracle, tolerances):
    facts = {}
    for field, values, ref in zip(("raw_logits", "processed_scores"), device, oracle):
        if ref is None:
            facts[field] = {"status": "not_evaluated", "reason": "CPU 没有同一选词规则的参考概率"}
            continue
        token_id, error = peak_error(values, ref)
        facts[field] = {"token_id": token_id, "max_abs_error": error, "within_tolerance": error <= tolerances[field]}
    return facts


def reference(req, gpu, report, position):
    if "cpu_model" in req:
        prefix = gpu.value["prompt_token_ids"] + gpu.value["output_token_ids"][:position - 1]
        oracle, metadata = forward(req["cpu_model"], prefix)
        report["evidence_kind"]["cpu"] = "local_cpu_forward"
        return oracle, metadata
    artifact = bound(req["cpu"])
    check_prefix(artifact.value, gpu, position, report["subset"])
    row = artifact.value
    oracle = step_vectors(row["raw_logits"], row["processed_probs"], Path(artifact.ref["path"]).parent)
    report["evidence"]["cpu"] = artifact.ref
    report["evidence_kind"]["cpu"] = row.get("evidence_kind", "unspecified")
    return oracle, {}


def verify_cpu(req, devices, report, tolerances):
    if report["result"] != "mismatch":
        return {"status": "not_applicable", "reason": "没有具有双端预测向量的首个可疑位置"}
    if "cpu" not in req and "cpu_model" not in req:
        return {"status": "not_evaluated", "reason": "未提供该位置的 CPU 参考"}
    position = report["first_anomalous_token"]
    try:
        oracle, metadata = reference(req, devices["gpu"], report, position)
        checked = {side: measure(read_step(data, position - 1), oracle, tolerances)
                   for side, data in devices.items()}
    except Exception as exc:
        # 可选的 CPU 依赖、模型实现和文件来源各有自己的异常，不撤销已核实的双端首差。
        return {"status": "not_evaluated", "position": position, "reason": str(exc)}
    return {"status": "completed", "position": position, **metadata, **checked}
