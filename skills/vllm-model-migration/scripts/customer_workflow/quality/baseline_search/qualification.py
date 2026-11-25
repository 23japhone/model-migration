"""从原始发布材料核对 GPU 历史测量，并绑定本次已验证部署的真实权重。"""
import hashlib
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from coordination.runtime import sources
from coordination.runtime.receipts import VERIFY_KEYS
from skill_paths import skill_root
from ...state import identity
from ..inputs import normalize_one
from .publisher import LINK, pointer


SHA256 = re.compile(r"[a-f0-9]{64}")
REVISION = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
GENERATION = ("temperature", "top_p", "top_k", "max_tokens", "seed")
PROMPT = ("request_format", "system_prompt", "enable_thinking", "prompt_template", "chat_template")


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


def model_files(task, origin):
    model = deployed_model(origin.get("deployment_receipt"))
    if model["name"] != task["model"]["model_name"]:
        raise ValueError("部署回执与本次模型名不符")
    files = model["files"]
    if not files or not all(is_digest(dig) for dig in files.values()):
        raise ValueError("部署回执没有固定模型文件")
    weights = model.get("weight_files")
    if not isinstance(weights, list) or not weights or set(weights) - files.keys():
        raise ValueError("部署回执没有固定完整权重")
    return identity(files)


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


def generation_problems(protocol, static):
    reasons = []
    server = static.get("server_generation") or {}
    files = server.get("model_generation_files")
    if not (server.get("overrides") or {}).get("--generation-config") or not isinstance(files, dict) or not all(is_digest(v) for v in files.values()):
        reasons.append("服务端生效生成配置")
    limit = protocol["generation_config"].get("max_tokens") or static.get("effective_max_tokens")
    if type(limit) is not int or limit < 1:
        reasons.append("实际输出预算")
    return reasons


def random_selection_known(protocol, static):
    prompt = protocol["prompt_config"]
    if prompt.get("few_shot_random") is not True or prompt.get("few_shot_num") == 0:
        return True
    if type(prompt.get("few_shot_seed")) is int:
        return True
    selected = static.get("reader_sha256_by_subset") or {}
    return all(is_digest(selected.get(subset)) for subset in protocol["subsets"])


def static_problems(protocol, static):
    reasons = []
    if not is_digest(static.get("chat_template_sha256")) or not is_digest(static.get("tokenizer_sha256")):
        reasons.append("静态提示模板或分词器身份")
    reasons.extend(generation_problems(protocol, static))
    if not random_selection_known(protocol, static):
        reasons.append("随机 few-shot 的实际示例或选择种子")
    if protocol["prompt_config"].get("request_format") != "chat":
        reasons.append("受支持的原始请求和提示配置")
    scoring = protocol.get("scoring_config") or {}
    if (scoring.get("judge") or {}).get("strategy") != "rule":
        reasons.append("可重跑的原生评分规则")
    return reasons


def declared_scores(report):
    scores = []
    for score in report["scores"]:
        if score.get("error") or score.get("value") is None:
            scope = score.get("subset") or "所选范围总分"
            reason = score.get("error") or "缺少原生评分值"
            raise ValueError(f"公开 GPU 指标 {score['metric']}（{scope}）无法核验：{reason}")
        entry = {"metric": score["metric"], "gpu_score": score["value"]}
        if "published_target" in score:
            entry["target"] = score["published_target"]["target"]
        if score["subset"] is not None:
            entry["subset"] = score["subset"]
        scores.append(entry)
    if not scores:
        raise ValueError("缺少有原生指标、聚合和单位依据的 GPU 成绩")
    return scores


def declaration(report):
    p = report["protocol"]
    generation = {key: p["generation_config"][key] for key in GENERATION if p["generation_config"].get(key) is not None}
    if p["generation_config"].get("stop_seqs"):
        generation["stop"] = p["generation_config"]["stop_seqs"]
    raw = {"evaluator": p["evaluator"], "evaluator_version": p["evaluator_version"], "dataset": p["dataset"],
           "task_name": p["task_name"], "task_revision": p["task_revision"], "subset_list": p["subsets"],
           "prompt_config": {k: p["prompt_config"][k] for k in PROMPT if k in p["prompt_config"]},
           "generation_config": generation, "scores": declared_scores(report)}
    if "declaration" in p["scoring_config"]:
        raw["scoring_config"] = p["scoring_config"]["declaration"]
    return normalize_one(raw)


def qualify(task, origin, gpu_evidence):
    try:
        model_origin(task["model"], origin)
        expected_model = model_files(task, origin)
        checked = inspected(gpu_evidence)
        protocol = checked["protocol"]
        static = checked.get("static_conditions") or {}
        missing = scope_problems(protocol) + static_problems(protocol, static)
        if not matches_gpu_model(task, origin["repository_id"], checked):
            missing.append("原始 GPU 任务模型与发布仓库或本次模型不匹配")
        if protocol.get("model_identity") != expected_model:
            missing.append("原始 GPU 模型权重与本次部署不匹配")
        if missing:
            return {"baseline": None, "missing": missing}
        targets = [{"metric":s["metric"], "subset":s["subset"], **s["published_target"]}
                   for s in checked["scores"] if "published_target" in s]
        return {"baseline": declaration(checked), "protocol": protocol, "static_conditions": static, "published_targets":targets,
                "evidence": gpu_evidence, "missing": []}
    except (*sources.ERRORS, AttributeError, subprocess.SubprocessError) as error:
        return {"baseline": None, "missing": [str(error) or type(error).__name__]}
