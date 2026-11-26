"""从原始发布材料核对 GPU 历史测量，并绑定本次已验证部署的真实权重。"""
import hashlib
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from coordination.runtime import sources
from coordination.runtime.receipts import VERIFY_KEYS
from skill_paths import skill_root
from ..inputs import normalize_one
from .publisher import LINK, pointer


SHA256 = re.compile(r"[a-f0-9]{64}")
REVISION = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def is_digest(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def model_origin(model, origin):
    repo = origin["repository_id"]
    if not isinstance(repo, str) or not NAME.fullmatch(repo) or ".." in repo:
        raise ValueError("缺少唯一的公开模型仓库")
    specified = model.get("model_repository_id")
    if specified is None or specified == -1:
        folded = str(model.get("model_name", "")).casefold()
        if folded not in (repo.casefold(), repo.split("/")[-1].casefold()):
            raise ValueError("模型名未唯一对应公开模型仓库")
    elif specified != repo:
        raise ValueError("发布仓库与本次模型仓库不匹配")
    if origin.get("repository_url") != "https://huggingface.co/" + repo:
        raise ValueError("模型发布来源仓库不匹配")
    revision = origin.get("revision")
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise ValueError("公开模型来源缺少不可变快照")
    public_artifact(origin, repo, revision)


def public_artifact(origin, repo, revision):
    artifact = urlsplit(origin["artifact_url"])
    host = artifact.netloc
    path = unquote(artifact.path)
    if artifact.scheme != "https" or artifact.query or artifact.fragment or ".." in path.split("/"):
        raise ValueError("公开评测归档 URL 不可核验")
    prefix = "/" + repo + "/resolve/" + revision + "/"
    if host == "huggingface.co" and path.startswith(prefix) and path[len(prefix):]:
        return
    check_card_link(origin, prefix)


def check_card_link(origin, prefix):
    target = pointer(origin["artifact_url"])
    if not target or not REVISION.fullmatch(target[2]) or not target[3]:
        raise ValueError("外部归档没有固定到不可变版本")
    card = origin.get("model_card") or {}
    if card.get("url") != "https://huggingface.co" + prefix + "README.md":
        raise ValueError("外部归档未关联到官方模型卡的固定快照")
    ref = card["payload"]
    raw = sources.stable_bytes(ref["path"], 16 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("模型卡原始内容已变更")
    linked = (pointer(url) for url in LINK.findall(raw.decode("utf-8-sig")))
    if not any(item and item[:2] == target[:2] for item in linked):
        raise ValueError("原始模型卡未链接该评测资料仓库")


def deployed_model(ref):
    if not ref:
        raise ValueError("缺少本次已验证部署的权重身份")
    report = sources.bound(ref)
    if report.get("task_id") != "verify-agent" or report.get("status") != "completed" or report.get("success") is not True:
        raise ValueError("模型部署验证未通过")
    if any(report["conclusions"].get(key) != "pass" for key in VERIFY_KEYS):
        raise ValueError("模型部署验证存在未通过的结论")
    original = sources.bound(report["inputs"]["source"]) if report.get("operation") == "review" else report
    if (original.get("task_id"), original.get("operation"), original.get("status")) != ("verify-agent", "run", "completed"):
        raise ValueError("缺少原始部署验证回执")
    if report["evidence"]["handoff"] != original["evidence"]["handoff"]:
        raise ValueError("原始部署验证与复核的交接冲突")
    handoff = sources.bound({"path": original["evidence"]["handoff"], "sha256": original["detailed_report"]["handoff_sha256"]})
    if handoff["model"] != original["inputs"]["model"] or handoff["final_service"]["state"] != "retained":
        raise ValueError("已验证部署的交接与原始模型不一致或服务未保留")
    return handoff["model"]


def matches_gpu_model(task, repo, report):
    config = sources.bound(report["evidence"]["gpu_config"])
    name = config.get("model") or ""
    if NAME.fullmatch(name) and name != repo:
        return False
    known_names = (repo, repo.split("/")[-1], task["model"]["model_name"])
    return name in known_names or config.get("model_id") in known_names


def inspected(ref):
    sources.bound(ref)
    entry = skill_root("vllm-precision-compare") / "scripts/compare.py"
    result = subprocess.run([sys.executable, "-B", str(entry), "--inspect-gpu", ref["path"]],
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        raise ValueError("原生 GPU 成绩未获证实：" + (result.stderr.strip()[:600] or str(result.returncode)))
    checked = sources.parse(result.stdout)
    if checked["evidence"].get("gpu_evidence") != ref:
        raise ValueError("GPU 历史资料核验期间发生变化")
    return checked


def scope_problems(protocol):
    reasons = []
    revision = protocol.get("dataset_revision") or ""
    if not is_digest(protocol.get("dataset_identity")) or not REVISION.fullmatch(revision):
        reasons.append("数据集版本或原始内容摘要")
    problem = sample_scope_problem(protocol)
    if problem:
        reasons.append(problem)
    if any(not isinstance(protocol.get(k), str) or not protocol[k].strip()
           for k in ("dataset", "split", "task_name", "task_revision", "evaluator_version")):
        reasons.append("数据集 split、任务或评测器版本")
    if protocol.get("evaluator") != "evalscope":
        reasons.append("受支持的原生评测程序")
    return reasons


def sample_scope_problem(protocol):
    subsets = protocol.get("subsets")
    if not isinstance(subsets, list) or not subsets or not all(isinstance(s, str) and s.strip() for s in subsets):
        return "子集范围"
    if len(set(subsets)) != len(subsets):
        return "重复子集"
    identities = protocol.get("sample_identities")
    if not isinstance(identities, dict) or set(subsets) != set(identities) or not all(is_digest(x) for x in identities.values()):
        return "实际样本范围及身份"
    return None


def declared_scores(report):
    scores, missing = [], []
    for score in report["scores"]:
        if score.get("error") or score.get("value") is None:
            missing.append({"metric": score["metric"], "subset": score["subset"], "gpu_score": None})
            continue
        entry = {"metric": score["metric"], "gpu_score": score["value"]}
        if "published_target" in score:
            entry["target"] = score["published_target"]["target"]
        if score["subset"] is not None:
            entry["subset"] = score["subset"]
        scores.append(entry)
    if not scores:
        raise ValueError("缺少有原生指标、聚合和单位依据的 GPU 成绩")
    return scores, missing


def declaration(report):
    p = report["protocol"]
    scores, missing = declared_scores(report)
    raw = {"evaluator": p["evaluator"], "evaluator_version": p["evaluator_version"],
           "dataset": p["dataset"], "subset_list": p["subsets"], "scores": scores}
    baseline = normalize_one(raw)
    baseline["scores"].extend(missing)
    return baseline


def qualify(task, origin, gpu_evidence):
    try:
        model_origin(task["model"], origin)
        checked = inspected(gpu_evidence)
        protocol = checked["protocol"]
        static = checked.get("static_conditions") or {}
        missing = scope_problems(protocol)
        if not matches_gpu_model(task, origin["repository_id"], checked):
            missing.append("原始 GPU 任务模型与发布仓库或本次模型不匹配")
        if missing:
            return {"baseline": None, "missing": missing}
        targets = [{"metric":s["metric"], "subset":s["subset"], **s["published_target"]}
                   for s in checked["scores"] if "published_target" in s]
        return {"baseline": declaration(checked), "protocol": protocol, "static_conditions": static, "published_targets":targets,
                "evidence": gpu_evidence, "missing": [f"{row['metric']} ({row['subset']})：{row['error']}"
                                                       for row in checked["scores"] if row.get("error")]}
    except (*sources.ERRORS, AttributeError, subprocess.SubprocessError) as error:
        return {"baseline": None, "missing": [str(error) or type(error).__name__]}
