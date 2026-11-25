"""通过公开 CLI 验证任务质量判定。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
COMPARE = (ROOT / "skills/vllm-model-migration/skills/vllm-precision-align/skills"
           / "vllm-precision-compare/scripts/compare.py")


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def ref(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class QualityComparison(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.protocol = {"evaluator": "evalscope", "dataset": "ceval", "subsets": ["a", "b"],
                         "dataset_revision": "v1", "task_revision": "v1", "task_name": "ceval",
                         "model_identity": "example-model", "split": "test", "dataset_identity": "digest",
                         "sample_identities": {"a": "hash-a", "b": "hash-b"}, "scoring_config": {"judge": {"strategy":"rule"}},
                         "metrics": {"accuracy": {"selector": {"name": "acc", "aggregation": "mean"},
                                                 "unit": "ratio", "direction": "higher", "sample_metric": "acc", "sample_kind": "binary"}},
                         "evaluator_version": "1.0.0", "prompt_config": {"request_format": "chat"},
                         "generation_config": {"temperature": 0, "max_tokens":256}}
        self.gpu = {"format": "task-quality-native-v1", "hardware": "gpu", "protocol": self.protocol, "scores": [self.measured(None, 0.8),
                                                         self.measured("a", 0.5)]}
        self.npu = {"format": "task-quality-native-v1", "hardware": "npu", "protocol": self.protocol, "scores": [self.measured(None, 0.7),
                                                         self.measured("a", 0.7)]}
        self.runtime = {"chat_template_sha256": "b"*64,
                        "tokenizer_sha256": "c"*64,
                        "server_generation": {"overrides": {"--generation-config": "auto"}, "model_generation_files": {}}}
        self.npu["runtime_conditions"] = self.runtime
        self.gpu["runtime_conditions"] = self.runtime
        self.entry = {"dataset": "ceval", "evaluator": "evalscope", "subset_list": ["a", "b"],
                      "prompt_config": {"request_format": "chat"},
                      "generation_config": {"temperature": 0},
                      "scores": [{"metric": "accuracy", "gpu_score": 0.8, "target": {"max_drop": 0.1}},
                                 {"subset": "a", "metric": "accuracy", "gpu_score": 0.5,
                                  "target": {"max_drop": 0}}]}

    def measured(self, subset, value):
        return {"subset": subset, "metric": "accuracy", "value": value, "unit": "ratio",
                "direction": "higher", "aggregation": "mean", "sample_kind": "binary",
                "samples": []}

    def native_receipt(self, path, artifact, hardware):
        return save(path, {"status": "completed", "task_id": "verify-agent-plus", "operation": "quality-run",
                           "conclusions": {"evaluation_validity": "pass"}, "detailed_report": {"hardware": hardware},
                           "evidence": {"quality_evidence": ref(artifact)}})

    def request(self, historical=True):
        receipt = self.root / "receipt.json"
        measurement = self.root / "measurement.json"
        save(measurement, self.npu)
        self.native_receipt(receipt, measurement, "npu")
        req = {"baseline": self.entry, "measurement": str(measurement),
               "receipt": str(receipt), "source": "rerun"}
        if historical:
            req["gpu_evidence"] = self.external_index_path()
        return req

    def compare(self, historical=True):
        return self.invoke(self.request(historical))

    def invoke(self, req):
        req = save(self.root / "request.json", req)
        output = self.root / "comparison.json"
        proc = subprocess.run([sys.executable, "-B", COMPARE, "--request", req, "--output", output],
                              capture_output=True, text=True, check=False)
        self.assertTrue(output.exists(), proc.stderr + proc.stdout)
        return json.loads(output.read_text())

    def test_decimal_threshold_and_subset_improvement(self):
        report = self.compare()
        self.assertEqual(["pass", "pass"], [r["precision_target"] for r in report["scores"]])
        self.assertEqual(0.1, report["scores"][0]["drop"])

    def test_no_historical_evidence_keeps_real_measurement(self):
        report = self.compare(historical=False)
        self.assertEqual("not_evaluated", report["scores"][0]["precision_target"])
        self.assertEqual(0.7, report["scores"][0]["npu_score"])

    def test_one_conflicting_gpu_score_does_not_block_other_scope(self):
        self.gpu["scores"][0]["value"] = 0.75
        report = self.compare()
        self.assertEqual(["not_evaluated", "pass"], [r["precision_target"] for r in report["scores"]])

    def sample_rows(self, scope, gpu_rows, npu_rows, declared):
        self.entry["scores"][scope]["sample_results"] = declared
        self.gpu["scores"][scope]["samples"] = gpu_rows
        self.npu["scores"][scope]["samples"] = npu_rows

    def test_binary_changes_and_ambiguous_across_subsets(self):
        gpu = [{"subset": scope, "sample_id": item, "score": val}
               for scope, item, val in (("a", 1, 1), ("b", 1, 0), ("b", 2, 0))]
        npu = [{**row, "score": 1 - row["score"]} for row in gpu]
        self.sample_rows(0, gpu, npu, [gpu[0], {"sample_id": 2, "score": False}, {"sample_id": 1, "score": 1}])
        result = self.compare()["scores"][0]["sample_comparison"]
        self.assertEqual(("partial", 2, 3), (result["status"], result["matched"], result["expected"]))
        self.assertEqual(["correct_to_wrong", "wrong_to_correct"], [row["change"] for row in result["items"]])
        self.assertEqual(1, len(result["unmatched"]))

    def test_partial_gpu_sample_conflict_and_missing_npu(self):
        gpu = [{"sample_id": "x", "score": 1}, {"sample_id": "y", "score": 0}]
        self.sample_rows(1, gpu, [gpu[0]], [{"sample_id": "x", "score": 0}, gpu[1]])
        result = self.compare()["scores"][1]
        self.assertEqual("not_evaluated", result["sample_comparison"]["status"])
        self.assertEqual(2, len(result["sample_comparison"]["unmatched"]))
        self.assertEqual("pass", result["precision_target"])

    def test_numeric_lower_and_missing_target(self):
        del self.entry["scores"][0]["target"]
        self.protocol["metrics"]["accuracy"].update(direction="lower", sample_kind="numeric")
        for evidence in (self.gpu, self.npu):
            for row in evidence["scores"]:
                row.update(sample_kind="numeric", direction="lower")
        self.sample_rows(1, [{"sample_id": "a", "score": 0.4}], [{"sample_id": "a", "score": 0.7}],
                         [{"sample_id": "a", "score": 0.4}])
        r = self.compare()["scores"]
        self.assertEqual(("not_evaluated", -0.1), (r[0]["precision_target"], r[0]["drop"]))
        self.assertEqual(("fail", 0.2), (r[1]["precision_target"], r[1]["drop"]))
        self.assertEqual(0.3, r[1]["sample_comparison"]["items"][0]["npu_minus_gpu"])
        report = json.loads((self.root/"comparison.json").read_text())
        self.assertTrue(report["next_action"]["requires_separate_run"])
        self.assertEqual(["Token 预测对齐", "张量与算子对齐"], report["next_action"]["tasks"])

    def test_asymmetric_two_sides_do_not_match_on_alias_only(self):
        self.gpu["protocol"] = {**self.protocol, "evaluator_version": "0.9"}
        r = self.compare()
        self.assertEqual("review_required", r["conclusions"]["comparability"])
        self.assertEqual("not_evaluated", r["conclusions"]["precision_target"])

    def test_invalid_gpu_binding_retains_npu(self):
        req = self.request()
        source = self.root / "report.json"
        save(source, self.npu)
        result = self.invoke(req)
        self.assertEqual("not_evaluated", result["scores"][0]["precision_target"])
        self.assertEqual(0.7, result["scores"][0]["npu_score"])
        self.assertIn("摘要", result["uncovered"][0])

    def test_analyze_verification_binds_original_run(self):
        req = self.request()
        source = self.root / "receipt.json"
        old = json.loads(source.read_text())
        old.update(operation="run", evidence={})
        save(source, old)
        checked = self.root / "review.json"
        save(checked, {**old, "operation": "quality-analyze", "inputs": {"source": ref(source)},
                       "evidence": {"quality_evidence": ref(Path(req["measurement"]))}})
        req.update(verification=str(checked), source="reused")
        report = self.invoke(req)
        self.assertEqual("pass", report["conclusions"]["precision_target"])
        self.assertEqual(ref(checked), report["evidence"]["verification"])

    def external_index_path(self):
        p = self.gpu["protocol"]
        config = self.root / "config.json"
        save(config, {"model_id": "my-model", "dataset_args": {"ceval": {"eval_split": "test", "subset_list": ["a","b"],
                        **{k: v for k,v in p["prompt_config"].items() if k.startswith("few_shot_")}}},
                      "generation_config": p["generation_config"], "judge": {"strategy": "rule"}, "eval_type":"openai_api"})
        all_rows = {subset: {} for subset in p["subsets"]}
        for scope in self.gpu["scores"]:
            for row in scope["samples"]:
                subset = row.get("subset", scope["subset"])
                all_rows[subset][row["sample_id"]] = row["score"]
        ids, reviews, counts = {}, {}, {}
        for subset, samples in all_rows.items():
            if not samples:
                samples["unused-" + subset] = 0
            ids[subset] = list(samples)
            items = [{"index": i, "sample_score": {"sample_id": i, "score": {"status": "success", "value":{"acc": v}}}}
                     for i, v in enumerate(samples.values())]
            path = self.root / (subset + ".jsonl")
            path.write_text("\n".join(json.dumps(item) for item in items), encoding="utf-8")
            reviews[subset] = ref(path)
            counts[subset] = {"requested": len(items), "succeeded":len(items), "errored":0}
        total = sum(item["requested"] for item in counts.values())
        val = {row["subset"]:row["value"] for row in self.gpu["scores"]}
        metric = {"identity":p["metrics"]["accuracy"]["selector"], "semantics": {"kind":"quality", "raw_unit":"ratio",
                  "direction": p["metrics"]["accuracy"]["direction"] + "_is_better"}, "score":val[None], "num": total,
                  "categories":[{"subsets":[{"name":k, "score":val.get(k,1), "num":len(ids[k])} for k in ids]}]}
        report = self.root / "report.json"
        save(report, {"schema_version":2, "dataset_name":"ceval", "model_name":"my-model", "metrics":[metric],
                      "execution_summary":{"requested":total,"succeeded":total,"errored":0,"incomplete":False,"subsets":counts}})
        files = [ref(report),ref(config),*reviews.values()]
        source = self.root / "original-run.json"
        save(source, {"run_id":"gpu-run-9", "hardware":"gpu", "status":"completed","protocol":p,"sample_ids":ids, "runtime_conditions": self.gpu.get("runtime_conditions"),
                      "files":{row["path"]:row["sha256"] for row in files}})
        return save(self.root / "external-index.json", {"format":"evalscope-gpu-history-v1", "source":ref(source),
                                                       "config":ref(config), "report":ref(report), "reviews":reviews})

    def external_index(self):
        return self.request()

    def test_external_original_evalscope_artifacts(self):
        self.entry["scores"][1]["sample_results"] = [{"sample_id":"original-id", "score": 1}]
        self.gpu["scores"][1]["samples"] = self.entry["scores"][1]["sample_results"]
        self.npu["scores"][1]["samples"] = [{"sample_id":"original-id", "subset": "a", "score": 0}]
        r = self.invoke(self.external_index())
        self.assertEqual("pass", r["conclusions"]["precision_target"], r)
        self.assertEqual("correct_to_wrong", r["scores"][1]["sample_comparison"]["items"][0]["change"])

    def test_external_report_must_match_same_run_not_just_mutable_index(self):
        req = self.external_index()
        report = self.root / "report.json"
        save(report, {"rewritten": True})
        index = self.root / "external-index.json"
        save(index, {**json.loads(index.read_text()), "report": ref(report)})
        r = self.invoke(req)
        self.assertEqual("not_evaluated", r["conclusions"]["precision_target"])
        self.assertIn("GPU", r["uncovered"][0])

    def test_publisher_origin_attached_to_matching_precision_target(self):
        req = self.request()
        index_path = Path(req["gpu_evidence"])
        index = json.loads(index_path.read_text())
        source = Path(index["source"]["path"])
        row = json.loads(source.read_text())
        save(source, {**row, "scores":[self.entry["scores"][0]]})
        save(index_path, {**index, "source":ref(source)})
        outcome = self.invoke(req)
        self.assertEqual("pass", outcome["conclusions"]["precision_target"])
        self.assertEqual("publisher", outcome["scores"][0]["target_evidence"]["kind"])
        self.assertEqual(ref(source), outcome["scores"][0]["target_evidence"]["source"])
        self.assertNotIn("target_evidence", outcome["scores"][1])

    def test_standalone_gpu_projection_and_self_declared_receipt_are_insufficient(self):
        req = self.request(historical=False)
        source = self.root / "gpu-projection.json"
        save(source, self.gpu)
        req["gpu_evidence"] = self.native_receipt(self.root / "gpu-receipt.json", source, "gpu")
        self.assertEqual("not_evaluated", self.invoke(req)["conclusions"]["precision_target"])

    def test_empty_and_duplicate_sample_results_leave_totals_valid(self):
        self.entry["scores"][0]["sample_results"] = []
        self.entry["scores"][1]["sample_results"] = [{"sample_id": "x", "score": 1}] * 2
        result = self.compare()
        self.assertEqual("pass", result["conclusions"]["precision_target"])
        first, second = (row["sample_comparison"] for row in result["scores"])
        self.assertEqual(("not_evaluated", 0), (first["status"], first["matched"]))
        self.assertEqual(("not_evaluated", 2), (second["status"], second["unmatched_count"]))

    def test_missing_static_template_and_settings_cannot_pass(self):
        self.gpu.pop("runtime_conditions")
        report = self.compare()
        self.assertEqual("not_evaluated", report["conclusions"]["precision_target"])
        self.assertIn("实际", report["uncovered"][0])

    def test_runtime_template_changed_despite_same_taskconfig(self):
        self.gpu["runtime_conditions"] = {**self.runtime, "chat_template_sha256": "f"*64}
        report = self.compare()
        self.assertEqual("review_required", report["conclusions"]["comparability"])
        self.assertEqual(0.7, report["scores"][0]["npu_score"])

    def test_alignment_does_not_require_request_or_token_fingerprints(self):
        self.npu["runtime_conditions"] = {**self.runtime, "request_conditions_sha256": "1"*64}
        self.gpu["runtime_conditions"] = dict(self.runtime)
        report = self.compare()
        self.assertEqual("pass", report["conclusions"]["precision_target"])
        self.assertNotIn("next_action", report)

    def test_native_missing_aggregate_preserves_valid_subset(self):
        req = self.request()
        report = self.root / "report.json"
        raw = json.loads(report.read_text())
        del raw["metrics"][0]["score"]
        save(report, raw)
        index = self.root / "external-index.json"
        original = self.root / "original-run.json"
        source = json.loads(original.read_text())
        source["files"][str(report)] = ref(report)["sha256"]
        save(original, source)
        save(index, {**json.loads(index.read_text()), "source": ref(original), "report": ref(report)})
        result = self.invoke(req)
        self.assertEqual(["not_evaluated", "pass"], [row["precision_target"] for row in result["scores"]])

    def test_omitted_effective_output_limit_keeps_npu_measurement(self):
        del self.protocol["generation_config"]["max_tokens"]
        result = self.compare()
        self.assertEqual("not_evaluated", result["conclusions"]["precision_target"])
        self.assertEqual(0.7, result["scores"][0]["npu_score"])
        self.assertTrue(any("输出预算" in text for text in result["uncovered"]))


    def test_input_fingerprint_conflicts_are_subset_specific(self):
        self.npu["runtime_conditions"] = {**self.runtime, "reader_sha256_by_subset":{"a":"1"*64,"b":"2"*64}}
        self.gpu["runtime_conditions"] = {**self.runtime, "reader_sha256_by_subset":{"a":"1"*64,"b":"3"*64}}
        self.assertEqual(["not_evaluated", "pass"], [r["precision_target"] for r in self.compare()["scores"]])

    def test_generation_seed_does_not_fix_fewshot_selection(self):
        self.protocol["prompt_config"].update(few_shot_random=True, few_shot_num=5)
        self.protocol["generation_config"]["seed"] = 42
        self.assertEqual("not_evaluated", self.compare()["conclusions"]["precision_target"])
        (self.root / "comparison.json").unlink()
        self.protocol["prompt_config"]["few_shot_seed"] = 0
        self.assertEqual("pass", self.compare()["conclusions"]["precision_target"])


if __name__ == "__main__":
    unittest.main()
