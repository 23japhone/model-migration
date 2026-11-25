"""按原生指标和作用范围比较已验证的 NPU/GPU 历史分数。"""
import re

from .samples import compare_samples, numeric


def matches_config(declared, actual):
    if isinstance(declared, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches_config(value, actual[key]) for key, value in declared.items())
    if type(declared) is bool or type(actual) is bool:
        return type(declared) is type(actual) and declared == actual
    return declared == actual


CORE = ("evaluator", "evaluator_version", "dataset", "dataset_revision", "task_name",
        "task_revision", "split", "model_identity", "dataset_identity", "prompt_config",
        "generation_config", "scoring_config")


def shared_conditions(actual, previous):
    problems = []
    for key in CORE:
        if key not in actual or actual[key] in (None, ""):
            problems.append(f"NPU 缺少评测条件 {key}")
        elif not matches_config(actual[key], previous.get(key)) or not matches_config(previous.get(key), actual[key]):
            problems.append(f"GPU/NPU 评测条件不同：{key}")
    return problems


def declared_conditions(declaration):
    # 客户 stop 是 OpenAI 请求字段，EvalScope 实际配置的字段是 stop_seqs。
    keys = ("dataset", "evaluator", "evaluator_version", "task_name", "task_revision", "prompt_config")
    result = {key: declaration[key] for key in keys if key in declaration}
    if "generation_config" in declaration:
        generation = dict(declaration["generation_config"])
        if "stop" in generation:
            stop = generation.pop("stop")
            generation["stop_seqs"] = [stop] if isinstance(stop, str) else stop
        result["generation_config"] = generation
    if declaration.get("scoring_config"):
        result["scoring_config"] = {"declaration": declaration["scoring_config"]}
    return result


def protocol_errors(declaration, actual, previous):
    problems = shared_conditions(actual, previous)
    for key, value in declared_conditions(declaration).items():
        if not matches_config(value, actual.get(key)):
            problems.append(f"NPU 实际条件与客户声明不同：{key}")
    subsets = actual.get("subsets")
    if not isinstance(subsets, list) or not subsets or previous.get("subsets") != subsets:
        problems.append("GPU/NPU 评测子集范围缺失或不一致")
    explicit = declaration.get("subset_list")
    if isinstance(explicit, list) and explicit != subsets:
        problems.append("客户声明与测量的子集范围不同")
    return problems


def static_errors(measured, historical):
    npu = measured.get("static_conditions") or measured.get("runtime_conditions") or {}
    gpu = historical.get("static_conditions") or historical.get("runtime_conditions") or {}
    reasons = []
    for field in ("chat_template_sha256", "tokenizer_sha256"):
        observed = npu.get(field)
        if not isinstance(observed, str) or not re.fullmatch("[0-9a-f]{64}", observed) or gpu.get(field) != observed:
            reasons.append("实际使用的静态模板或 tokenizer 配置缺失或不同：" + field)
    if not same_budget(measured["protocol"], npu, gpu):
        reasons.append("缺少两端相同且可核验的有效输出预算")
    current = npu.get("server_generation")
    previous = gpu.get("server_generation")
    if not valid_generation(current) or previous != current:
        reasons.append("实际服务生成参数及模型生成配置未经确认或不同")
    return reasons


def same_budget(protocol, npu, gpu):
    configured = (protocol.get("generation_config") or {}).get("max_tokens")
    if type(configured) is int and configured > 0:
        return True
    effective = npu.get("effective_max_tokens")
    return type(effective) is int and effective > 0 and type(gpu.get("effective_max_tokens")) is int and gpu["effective_max_tokens"] == effective


def valid_generation(values):
    if not isinstance(values, dict) or not isinstance(values.get("overrides"), dict):
        return False
    if not values["overrides"].get("--generation-config"):
        return False
    files = values.get("model_generation_files")
    return isinstance(files, dict) and all(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest)
                                           for digest in files.values())


def reader_conditions(measured, historical, scopes):
    prompt = measured["protocol"]["prompt_config"]
    random_examples = (prompt.get("few_shot_num") != 0 and prompt.get("few_shot_random") is True
                       and type(prompt.get("few_shot_seed")) is not int)
    current, previous = [(data.get("static_conditions") or data.get("runtime_conditions") or {}).get("reader_sha256_by_subset", {})
                         for data in (measured, historical)]
    for name in scopes:
        npu, gpu = current.get(name), previous.get(name)
        if npu is None or gpu is None:
            if random_examples:
                raise ValueError("缺少随机 few-shot 的实际示例或共同选择种子：" + name)
        elif npu != gpu or not isinstance(npu, str) or not re.fullmatch("[0-9a-f]{64}", npu):
            raise ValueError("评前输入与示例内容或顺序不一致：" + name)


def metric_protocol(actual, previous, metric, subset):
    metadata = actual.get("metrics", {}).get(metric)
    if not isinstance(metadata, dict) or previous.get("metrics", {}).get(metric) != metadata:
        raise ValueError("两端缺少相同且已解析的原生指标定义")
    if not metadata.get("selector"):
        raise ValueError("缺少原生指标选择依据")
    scopes = actual["subsets"] if subset is None else [subset]
    for name in scopes:
        data = actual.get("sample_identities", {}).get(name)
        if not data or data != previous.get("sample_identities", {}).get(name):
            raise ValueError("两端样本范围缺失或不一致：" + name)
    return metadata


def find_score(measured, declaration):
    scope = declaration.get("subset")
    name = declaration["metric"]
    candidates = [row for row in measured["scores"] if isinstance(row, dict) and
                  row.get("subset") == scope and row.get("metric") == name]
    if len(candidates) != 1:
        raise ValueError("该作用范围的原生指标缺失或不唯一")
    if "error" in candidates[0]:
        raise ValueError(candidates[0]["error"])
    return candidates[0]


def verify_metric(row, previous, metadata):
    for key in ("unit", "direction", "aggregation"):
        if not row.get(key) or row[key] != previous.get(key):
            raise ValueError("两端原生指标身份不一致：" + key)
    if any(metadata.get(key) != row[key] for key in ("unit", "direction")):
        raise ValueError("指标记录与原生定义不一致")
    if row["direction"] not in ("higher", "lower"):
        raise ValueError("原生指标方向无效")
    selector = metadata["selector"]
    if isinstance(selector, dict) and selector.get("aggregation") and selector["aggregation"] != row["aggregation"]:
        raise ValueError("指标记录与协议内聚合方式不符")


def target(row, declared, gpu, npu, direction):
    delta = npu - gpu
    drop = -delta if direction == "higher" else delta
    row.update(npu_minus_gpu=delta, drop=drop)
    if "target" in declared:
        limit = numeric(declared["target"]["max_drop"])
        if limit < 0:
            raise ValueError("允许下降量须非负")
        row["precision_target"] = "pass" if drop <= limit else "fail"


def samples(row, declared, gpu, npu):
    if "sample_results" not in declared:
        return
    try:
        if npu.get("sample_kind") != gpu.get("sample_kind"):
            raise ValueError("两端逐题评分类型不同")
        row["sample_comparison"] = compare_samples(declared, gpu, npu)
        if gpu.get("sample_errors"):
            row["sample_comparison"]["gpu_review_errors"] = gpu["sample_errors"]
    except (KeyError, ValueError, TypeError) as error:
        row["sample_comparison"].update(reason=str(error))


def score(declared, current, previous, issues):
    row = {"subset": declared.get("subset"), "metric": declared["metric"], "gpu_score": declared["gpu_score"],
           "npu_score": None, "comparability": "review_required", "precision_target": "not_evaluated",
           "reasons": list(issues)}
    if "target" in declared:
        row["target"] = declared["target"]
    if "sample_results" in declared:
        row["sample_comparison"] = {"status": "not_evaluated", "matched": 0,
                                    "expected": len(declared["sample_results"]), "items": [], "unmatched": []}
    try:
        npu = find_score(current, declared)
        measured = numeric(npu["value"])
        row.update(npu_score=measured, unit=npu.get("unit"), direction=npu.get("direction"),
                   aggregation=npu.get("aggregation"))
        if issues:
            return row
        gpu = find_score(previous, declared)
        metadata = metric_protocol(current["protocol"], previous["protocol"], row["metric"], row["subset"])
        scopes = current["protocol"]["subsets"] if row["subset"] is None else [row["subset"]]
        reader_conditions(current, previous, scopes)
        verify_metric(npu, gpu, metadata)
        given = numeric(declared["gpu_score"])
        if numeric(gpu.get("value")) != given:
            raise ValueError("GPU 声明分数与历史原生分数不同")
        public = gpu.get("published_target")
        if public and public["target"] == declared.get("target"):
            row["target_evidence"] = public
        target(row, declared, given, measured, npu["direction"])
        row["comparability"] = "pass"
        samples(row, declared, gpu, npu)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        row["reasons"].append(str(error))
    return row


def verdict(rows):
    if any(row["precision_target"] == "fail" for row in rows):
        return "fail"
    requested = [row["precision_target"] for row in rows if "target" in row]
    return "pass" if requested and all(value == "pass" for value in requested) else "not_evaluated"


def compare(req, measured, historical, refs, issues):
    baseline = req["baseline"]
    if historical is not None:
        issues.extend(protocol_errors(baseline, measured["protocol"], historical["protocol"]))
        issues.extend(static_errors(measured, historical))
    rows = [score(item, measured, historical, issues) for item in baseline["scores"]]
    comparable = "pass" if all(row["comparability"] == "pass" for row in rows) else "review_required"
    conclusions = {"evaluation_validity": "pass", "comparability": comparable, "precision_target": verdict(rows)}
    report = {"schema_version": "1.0", "task_id": "vllm-precision-compare", "operation": "run",
            "source": req["source"], "status": "completed", "baseline": baseline, "scores": rows,
            "conclusions": conclusions, "evidence": refs, "uncovered": issues}
    if conclusions["precision_target"] == "fail":
        report["next_action"] = {"stage": "精度比较", "tasks": ["Token 预测对齐", "张量与算子对齐"],
                                 "requires_separate_run": True,
                                 "failed_scores": [{"dataset": baseline["dataset"], "subset": row["subset"],
                                                    "metric": row["metric"]} for row in rows
                                                   if row["precision_target"] == "fail"]}
    return report
