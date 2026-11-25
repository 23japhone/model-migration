"""只读核对原始 GPU/NPU 测量回执、复核回执及其固定的证据文件。"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 字段：" + key)
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("非法 JSON 数字：" + value)


def path_of(value):
    if not isinstance(value, str) or not value:
        raise ValueError("缺少文件路径")
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("须提供未重定向的规范绝对路径：" + value)
    return path


@dataclass
class Artifact:
    value: dict
    ref: dict


def read(value):
    path = path_of(value)
    data = path.read_bytes()
    obj = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant, parse_float=Decimal)
    if not isinstance(obj, dict):
        raise ValueError("文件顶层应是 JSON 对象：" + path.name)
    return Artifact(obj, {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})


def bound(ref):
    if not isinstance(ref, dict):
        raise ValueError("缺少历史文件引用")
    artifact = read(ref.get("path"))
    if artifact.ref != ref:
        raise ValueError("历史文件摘要不匹配")
    return artifact


def receipt(artifact, hardware, operations):
    row = artifact.value
    if row.get("task_id") != "verify-agent-plus" or row.get("operation") not in operations:
        raise ValueError("来源回执任务或操作类型不符")
    if row.get("status") != "completed" or row.get("conclusions", {}).get("evaluation_validity") != "pass":
        raise ValueError("来源未产生有效测量")
    if row.get("detailed_report", {}).get("hardware") != hardware:
        raise ValueError("测量设备来源不符")


def projection(artifact, hardware):
    source = bound(artifact.value.get("evidence", {}).get("quality_evidence"))
    data = source.value
    if data.get("format") != "task-quality-native-v1" or data.get("hardware") != hardware:
        raise ValueError("原生任务质量证据格式或设备不符")
    if not isinstance(data.get("protocol"), dict) or not isinstance(data.get("scores"), list):
        raise ValueError("原生任务质量证据缺少协议或指标记录")
    return source


def npu_evidence(req):
    original = read(req["receipt"])
    receipt(original, "npu", {"run", "quality-run"})
    refs = {"receipt": original.ref}
    checked = original
    if req.get("verification"):
        checked = read(req["verification"])
        if checked.ref != original.ref:
            receipt(checked, "npu", {"quality-analyze"})
            if checked.value.get("inputs", {}).get("source") != original.ref:
                raise ValueError("复核回执未绑定原始 NPU 测量")
        refs["verification"] = checked.ref
    measured = projection(checked, "npu")
    if measured.ref["path"] != str(path_of(req["measurement"])):
        raise ValueError("原始/复核回执与指定的 NPU 成绩文件不同")
    refs["measurement"] = measured.ref
    return measured.value, refs


def gpu_evidence(path):
    evidence = read(path)
    from .archive import from_measurement, imported
    if evidence.value.get("format") == "evalscope-gpu-history-v1":
        return imported(evidence)
    original = bound(evidence.value["origin"]) if "origin" in evidence.value else evidence
    receipt(original, "gpu", {"run", "quality-run"})
    measured, refs = from_measurement(original)
    if evidence is not original:
        if evidence.value.get("protocol") != measured["protocol"]:
            raise ValueError("GPU 投影与原始回执绑定的评测协议不同")
    return measured, {**refs, "gpu_evidence": evidence.ref, "gpu_manifest": original.ref}
