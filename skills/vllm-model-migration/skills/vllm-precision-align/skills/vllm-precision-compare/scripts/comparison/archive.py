"""导入同次 GPU 评测留下的原生 EvalScope v2 配置、报告和逐题评分。"""
from decimal import Decimal
import hashlib
import json

from .evidence import bound, invalid_constant, path_of, unique
from .published_targets import annotate
from .samples import numeric


def archived_ref(ref, source):
    if not isinstance(ref, dict) or not isinstance(source.get("files"), dict):
        raise ValueError("GPU 历史来源缺少原生文件清单")
    if not ref.get("sha256") or source["files"].get(ref.get("path")) != ref["sha256"]:
        raise ValueError("GPU 原生文件的路径与摘要未绑定到同次历史运行")
    return ref


def native_conditions(config, protocol, index, source):
    if config.get("eval_type") != "openai_api" or config.get("eval_backend", "Native") != "Native":
        raise ValueError("历史 TaskConfig 未使用可核验的原生文本 API 评测")
    repeats = config.get("repeats", 1)
    if type(repeats) is not int or repeats != 1:
        raise ValueError("GPU 历史任务的重复评测次数与本任务的单轮协议不同")
    name = protocol["dataset"]
    args = config["dataset_args"][name]
    if args["subset_list"] != protocol["subsets"] or args["eval_split"] != protocol["split"]:
        raise ValueError("GPU 历史配置与记录的 split/subset 不符")
    generation = config["generation_config"]
    if generation != protocol["generation_config"]:
        raise ValueError("GPU 历史原生生成参数与协议不符")
    prompt = native_prompt(config, args, generation)
    scoring = {"judge": config["judge"], **{key: args[key] for key in ("filters", "metric_list", "aggregation")
                                           if key in args}}
    if "declaration" in protocol["scoring_config"]:
        scoring = declared_scoring(scoring, protocol, index, source)
    if prompt != protocol["prompt_config"] or scoring != protocol["scoring_config"]:
        raise ValueError("GPU 历史提示或评分配置与协议不同")


def native_prompt(config, args, generation):
    prompt_fields = ("system_prompt", "prompt_template", "few_shot_num", "few_shot_random", "few_shot_seed", "few_shot_prompt_template")
    prompt = {"request_format": "chat", **{key: args[key] for key in prompt_fields if key in args}}
    if "chat_template" in config:
        prompt["chat_template"] = config["chat_template"]
    kwargs = (generation.get("extra_body") or {}).get("chat_template_kwargs", {})
    if "enable_thinking" in kwargs:
        prompt["enable_thinking"] = kwargs["enable_thinking"]
    return prompt


def declared_scoring(scoring, protocol, index, source):
    proof = bound(archived_ref(index.get("scoring_proof"), source))
    adapter = archived_ref(index.get("adapter"), source)
    digest = hashlib.sha256(path_of(adapter["path"]).read_bytes()).hexdigest()
    expected = {"declaration": protocol["scoring_config"]["declaration"], "evalscope_version": protocol["evaluator_version"],
                "benchmark": protocol["dataset"], "adapter_sha256": digest, "scoring_config": scoring}
    if digest != adapter["sha256"] or proof.value != expected:
        raise ValueError("GPU 自定义评分规则缺少匹配的历史 adapter、版本及配置证明")
    return {**scoring, "declaration": expected["declaration"]}


def id_count(ids):
    if not isinstance(ids, list) or not ids:
        raise ValueError("缺少 GPU 历史样本的真实编号")
    if any(type(i) not in (str, int) or i == "" for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("GPU 历史样本编号无效或重复")
    return len(ids)


def coverage(report, protocol, sample_counts):
    summary = report["execution_summary"]
    subsets = protocol["subsets"]
    if set(sample_counts) != set(subsets) or set(summary["subsets"]) != set(subsets):
        raise ValueError("GPU 原生报告、历史样本清单与协议的子集范围不符")
    counts = {}
    for subset, count in sample_counts.items():
        if type(count) is not int or count <= 0:
            raise ValueError("GPU 历史样本数量无效")
        counts[subset] = count
        check_count(summary["subsets"][subset], count)
    check_count(summary, sum(counts.values()))
    if summary.get("incomplete") is not False:
        raise ValueError("GPU 历史原生评测未完整结束")
    return counts


def check_count(summary, n):
    if any(type(summary.get(key)) is not int or summary[key] != value
           for key, value in (("requested", n), ("succeeded", n), ("errored", 0))):
        raise ValueError("GPU 原生报告执行覆盖不完整")


def read_reviews(ref, source, sample_ids):
    archived_ref(ref, source)
    data = path_of(ref["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("GPU 原生 reviews 内容发生变化")
    rows = {}
    for line in data.splitlines():
        row = json.loads(line, parse_float=Decimal, parse_constant=invalid_constant, object_pairs_hook=unique)
        index = row["index"]
        sample = row["sample_score"]
        if type(index) is not int or index in rows or not 0 <= index < len(sample_ids):
            raise ValueError("GPU 原生 review index 重复或越界")
        if type(sample["sample_id"]) is not int or sample["sample_id"] != index:
            raise ValueError("GPU 原生 review 中的样本身份不一致")
        score = sample["score"]
        if score.get("status") != "success" or not isinstance(score.get("value"), dict):
            raise ValueError("GPU 原生逐题评分无效")
        rows[index] = score["value"]
    return [(sample_ids[index], values) for index, values in rows.items()]


def selected_metric(report, spec):
    matches = [row for row in report["metrics"] if row["identity"] == spec["selector"]]
    if len(matches) != 1:
        raise ValueError("GPU 原生指标名称、维度及聚合未唯一匹配")
    metric = matches[0]
    semantics = metric["semantics"]
    direction = {"higher": "higher_is_better", "lower": "lower_is_better"}[spec["direction"]]
    native = semantics.get("raw_unit")
    if native is None:
        components = semantics.get("semantic_id", "").split(".")
        native = components[-1] if len(components) >= 3 else None
    unit_matches = native == spec["unit"] or (spec["unit"] == "value" and native not in (None, "", "ratio", "percent"))
    if not unit_matches or semantics.get("kind") != "quality" or semantics.get("direction") != direction:
        raise ValueError("GPU 原生指标语义、方向或单位不符")
    return metric


def score_value(row, count, semantics):
    if type(row.get("num")) is not int or row["num"] != count:
        raise ValueError("GPU 原生评分分母与实际覆盖不同")
    score = numeric(row["score"])
    bounds = semantics.get("value_range")
    if bounds and not numeric(bounds["min"]) <= score <= numeric(bounds["max"]):
        raise ValueError("GPU 原生成绩超出声明范围")
    return score


def native_scopes(metric):
    result = {}
    for category in metric["categories"]:
        for row in category["subsets"]:
            if not row.get("is_aggregate"):
                result.setdefault(row["name"], []).append(row)
    return result


def scope_value(subset, metric, counts, parts):
    semantics = metric["semantics"]
    if subset is None:
        if set(parts) != set(counts):
            raise ValueError("GPU 原生总分的分项范围与要求不同")
        return score_value(metric, sum(counts.values()), semantics)
    values = {score_value(row, counts[subset], semantics) for row in parts.get(subset, [])}
    if len(values) != 1:
        raise ValueError("GPU 原生子集分数缺失或存在冲突")
    return values.pop()


def projected(report, protocol, counts, reviewed, review_errors):
    scores = []
    for name, spec in protocol["metrics"].items():
        try:
            metric = selected_metric(report, spec)
            parts = native_scopes(metric)
        except (AttributeError, ValueError, TypeError, KeyError) as error:
            scores.extend({"metric": name, "subset": subset, "error": str(error)} for subset in (None, *counts))
            continue
        for subset in (None, *counts):
            row = {"metric": name, "subset": subset}
            try:
                row["value"] = scope_value(subset, metric, counts, parts)
                relevant = counts if subset is None else [subset]
                row.update(scored_samples(spec, relevant, reviewed, review_errors))
            except (AttributeError, ValueError, TypeError, KeyError) as error:
                row["error"] = str(error)
            scores.append(row)
    return scores


def scored_samples(spec, scopes, reviews, errors):
    key = spec["sample_metric"]
    rows = [{"subset": scope, "sample_id": identity, "score": vals[key]}
            for scope in scopes for identity, vals in reviews.get(scope, []) if key in vals]
    return {"samples": rows, "aggregation": spec["selector"]["aggregation"],
            "sample_errors": {s: errors[s] for s in scopes if s in errors},
            **{k: spec[k] for k in ("unit", "direction", "sample_kind")}}


def imported(archive):
    index = archive.value
    origin = bound(index["source"])
    result, refs = native_files(index, origin)
    annotate(index, origin, result["scores"])
    return result, {"gpu_evidence": archive.ref, **refs}


def native_files(index, origin):
    source = origin.value
    if source.get("hardware") != "gpu" or source.get("status") != "completed" or not source.get("run_id"):
        raise ValueError("缺少已完成的原始 GPU 运行身份")
    config = bound(archived_ref(index["config"], source))
    report = bound(archived_ref(index["report"], source))
    p = source["protocol"]
    native_conditions(config.value, p, index, source)
    raw = report.value
    if p.get("evaluator") != "evalscope" or raw.get("schema_version") != 2 or raw.get("dataset_name") != p["dataset"]:
        raise ValueError("原生 GPU 评分报告的版本、工具或数据集不同")
    if not config.value.get("model_id") or raw.get("model_name") != config.value["model_id"]:
        raise ValueError("GPU 原始配置与报告的模型不同")
    ids = source.get("sample_ids", {})
    supplied = {key: id_count(rows) for key, rows in ids.items()}
    counts = coverage(raw, p, source.get("sample_counts", supplied))
    if any(count != counts.get(subset) for subset, count in supplied.items()):
        raise ValueError("GPU 历史稳定题号数量与评分分母不符")
    reviews, errors = available_reviews(index.get("reviews", {}), source, ids)
    result = projected(raw, p, counts, reviews, errors)
    runtime = source.get("static_conditions") or source.get("runtime_conditions")
    for key in ("static_conditions", "runtime"):
        if key in index:
            runtime = bound(archived_ref(index[key], source)).value
            break
    return ({"protocol": p, "scores": result, "static_conditions": runtime},
            {"gpu_receipt": origin.ref, "gpu_report": report.ref,
             "gpu_config": config.ref, "gpu_reviews": index.get("reviews", {})})


def available_reviews(references, source, ids):
    reviews, errors = {}, {}
    for subset, ref in references.items():
        try:
            if subset not in ids:
                raise ValueError("GPU 原生逐题评分缺少子集内源样本映射")
            reviews[subset] = read_reviews(ref, source, ids[subset])
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as error:
            errors[subset] = str(error)
    return reviews, errors


def from_measurement(receipt):
    """原始 quality-run 的内部文件形态，与外部原生归档使用相同读取过程。"""
    from .evidence import Artifact
    row = receipt.value
    if row.get("evidence", {}).get("gpu_history"):
        return imported(bound(row["evidence"]["gpu_history"]))
    details = row["detailed_report"]
    files = details.get("files")
    if not files:
        raise ValueError("GPU 回执缺少可重读的原生报告和配置")
    p = row["inputs"]["protocol"]
    root = path_of(details["scope_root"]) / "evalscope"
    model = details["tasks"][0]["model_abbr"]
    dataset = p["dataset"]
    def reference(path):
        return {"path": str(path), "sha256": files[str(path)]}
    config = path_of(receipt.ref["path"]).parent / "original-config.json"
    index = {"config": reference(config), "report": reference(root / "reports" / model / (dataset + ".json")),
             "reviews": {s: reference(root / "reviews" / model / f"{dataset}_{s}.jsonl") for s in p["subsets"]}}
    static = details.get("static_conditions") or details.get("runtime_conditions")
    if not static and row.get("evidence", {}).get("quality_evidence"):
        frozen = bound(row["evidence"]["quality_evidence"]).value
        if frozen.get("hardware") != "gpu" or frozen.get("protocol") != p:
            raise ValueError("GPU 原始测量中的冻结配置未绑定该次运行")
        static = frozen.get("static_conditions") or frozen.get("runtime_conditions")
    source = {"hardware": "gpu", "run_id": row["run_id"], "status": row["status"], "protocol": p,
              "files": files, "sample_ids": details["stable_samples"], "static_conditions": static}
    return native_files(index, Artifact(source, receipt.ref))
