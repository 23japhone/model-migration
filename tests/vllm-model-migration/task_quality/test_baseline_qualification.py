"""公开基线先验核验。真实档案和本地部署绑定分别来自独立的固定引用。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / "skills/vllm-model-migration"
sys.path.insert(0, str(ROOT / "scripts"))
from customer_workflow.quality.baseline_search.qualification import qualify
from customer_workflow.state import identity
from coordination.runtime.receipts import VERIFY_KEYS


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


class PublicBaselineQualification(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.task = {"model": {"model_repository_id": "org/model", "model_name": "served-model"}}
        files = {"model.safetensors": "1"*64, "config.json": "2"*64}
        model = {"name":"served-model","files":files,"weight_files":["model.safetensors"]}
        handoff = save(self.root / "handoff.json", {"model":model,"endpoint":"localhost",
                        "final_service":{"state":"retained"}})
        receipt = save(self.root / "verify.json", {"task_id":"verify-agent","operation":"run","status":"completed",
                      "success":True,"conclusions":dict.fromkeys(VERIFY_KEYS,"pass"),
                      "inputs":{"model":model,"service":{"endpoint":"localhost"}},"evidence":{"handoff":handoff["path"]},"detailed_report":{"handoff_sha256":handoff["sha256"]}})
        rev = "a"*40
        self.origin = {"repository_id":"org/model","revision":rev,"repository_url":"https://huggingface.co/org/model",
                       "artifact_url":"https://huggingface.co/org/model/resolve/"+rev+"/gpu-eval/index.json",
                       "deployment_receipt":receipt}
        self.selector = {"name":"acc","aggregation":"mean","dimensions":{}}
        self.proto = {"model_identity":identity(files),"dataset":"ceval","dataset_identity":"3"*64,
                      "dataset_revision":"4"*40,"evaluator":"evalscope","evaluator_version":"1.1.0",
                      "task_name":"ceval","task_revision":"v1","subsets":["a"],"split":"val",
                      "sample_identities":{"a":"5"*64},"generation_config":{"max_tokens":128,"temperature":0},
                      "prompt_config":{"request_format":"chat"},"scoring_config":{"judge":{"strategy":"rule"}},
                      "metrics":{"acc":{"selector":self.selector,"unit":"ratio","direction":"higher",
                                        "sample_kind":"binary","sample_metric":"acc"}}}
        self.static = {"chat_template_sha256":"6"*64,"tokenizer_sha256":"7"*64,
                       "server_generation":{"overrides":{"--generation-config":"auto"},"model_generation_files":{}}}

    def evidence(self, hardware="gpu", reported="served-model", config_model="org/model", repeats=1, published=()):
        cfg = save(self.root/"config.json",{"model":config_model,"model_id":"served-model","eval_type":"openai_api","repeats":repeats,
                   "dataset_args":{"ceval":{"subset_list":["a"],"eval_split":"val", **{k:v for k,v in self.proto["prompt_config"].items() if k.startswith("few_shot_")}}},
                   "judge":{"strategy":"rule"},"generation_config":self.proto["generation_config"]})
        stats={"requested":1,"succeeded":1,"errored":0}
        report = save(self.root/"report.json",{"schema_version":2,"model_name":reported,"dataset_name":"ceval",
                      "execution_summary":{**stats,"incomplete":False,"subsets":{"a":stats}},
                      "metrics":[{"identity":self.selector,"semantics":{"kind":"quality","raw_unit":"ratio",
                                "direction":"higher_is_better"},"score":0,"num":1,
                                  "categories":[{"subsets":[{"name":"a","num":1,"score":0}]}]}]})
        source = save(self.root/"gpu.json",{"hardware":hardware,"run_id":"run-4","status":"completed",
                    "protocol":self.proto,"static_conditions":self.static,"sample_counts":{"a":1},"scores":list(published),
                    "files":{r["path"]:r["sha256"] for r in [cfg,report]}})
        return save(self.root/"index.json",{"format":"evalscope-gpu-history-v1","config":cfg,"report":report,"source":source})

    def test_complete_original_run_zero_score_no_target(self):
        result = qualify(self.task, self.origin, self.evidence())
        self.assertEqual([], result["missing"])
        self.assertEqual(0, result["baseline"]["scores"][0]["gpu_score"])
        self.assertNotIn("target", result["baseline"]["scores"][0])
        self.assertEqual(["a"], result["baseline"]["subset_list"])

    def test_partial_native_metric_evidence_disqualifies_discovered_run(self):
        self.proto["metrics"]["unmatched"] = {
            **self.proto["metrics"]["acc"], "selector": {**self.selector, "name": "unreported"}}
        result = qualify(self.task, self.origin, self.evidence())
        self.assertIsNone(result["baseline"])
        self.assertIn("unmatched", " ".join(result["missing"]))

    def test_threshold_only_applies_to_matching_source_scope_and_score(self):
        row = {"metric":"acc", "gpu_score":0, "subset":"a", "target":{"max_drop":0}}
        ref = self.evidence(published=[row, {**row, "subset":None, "gpu_score":0.4}])
        result = qualify(self.task, self.origin, ref)
        total, subset = result["baseline"]["scores"]
        self.assertNotIn("target", total)
        self.assertEqual({"max_drop":0}, subset["target"])
        self.assertEqual("publisher", result["published_targets"][0]["kind"])
        self.assertEqual(json.loads(Path(ref["path"]).read_text())["source"], result["published_targets"][0]["source"])

    def test_unbound_index_threshold_is_ineligible(self):
        ref = self.evidence()
        index = json.loads(Path(ref["path"]).read_text())
        index["scores"] = [{"metric":"acc", "gpu_score":0, "target":{"max_drop":0.5}}]
        result = qualify(self.task, self.origin, save(self.root / "other-index.json", index))
        self.assertTrue(all("target" not in row for row in result["baseline"]["scores"]))

    def test_published_target_requires_one_nonnegative_number_in_same_run(self):
        row = {"metric":"acc", "gpu_score":0, "target":{"max_drop":0}}
        alternatives = [[row, row], [{**row, "run_id":"different"}], [{**row, "dataset":"another"}],
                        [{**row, "target":{"max_drop":True}}], [{**row, "target":{"max_drop":-0.1}}]]
        for rows in alternatives:
            with self.subTest(rows=rows):
                result = qualify(self.task, self.origin, self.evidence(published=rows))
                self.assertEqual([], result["published_targets"])
                self.assertNotIn("target", result["baseline"]["scores"][0])

    def test_only_unspecified_repository_allows_casefolded_model_origin(self):
        from customer_workflow.quality.baseline_search.qualification import model_origin
        model_origin({"model_name":"MoDeL"}, self.origin)
        with self.assertRaises(ValueError):
            model_origin({"model_name":"different"}, self.origin)
        with self.assertRaises(ValueError):
            model_origin({"model_name":"MoDeL", "model_repository_id":"Other/MoDeL"}, self.origin)

    def test_weight_mismatch_overrules_matching_model_name(self):
        self.proto["model_identity"] = "8"*64
        result = qualify(self.task,self.origin,self.evidence())
        self.assertIsNone(result["baseline"])
        self.assertTrue(result["missing"])

    def test_no_gpu_proof_or_immutable_origin_no_measurement(self):
        for origin, hardware in [(self.origin, "unknown"), ({**self.origin,"revision":"main"}, "gpu")]:
            with self.subTest(revision=origin["revision"]):
                self.assertIsNone(qualify(self.task, origin, self.evidence(hardware=hardware))["baseline"])

    def test_missing_dataset_or_effective_template_or_score_fails(self):
        self.proto["dataset_identity"] = ""
        self.static["chat_template_sha256"] = None
        result = qualify(self.task,self.origin,self.evidence())
        self.assertIsNone(result["baseline"])
        self.assertGreaterEqual(len(result["missing"]),2)

    def test_source_unchanged_but_derived_report_mismatch_rejected(self):
        evidence = self.evidence(reported="another-model")
        self.assertIsNone(qualify(self.task,self.origin,evidence)["baseline"])

    def test_incomplete_deployment_is_ineligible(self):
        path = Path(self.origin["deployment_receipt"]["path"])
        self.origin["deployment_receipt"] = save(path, {**json.loads(path.read_text()), "success": False})
        self.assertIsNone(qualify(self.task, self.origin, self.evidence())["baseline"])

    def test_conflicting_repository_despite_matching_report_alias_is_ineligible(self):
        self.assertIsNone(qualify(self.task, self.origin, self.evidence(config_model="other/model"))["baseline"])

    def test_external_release_requires_link_in_the_bound_card(self):
        card = self.root / "README.md"
        card.write_bytes(b"No external archive is linked")
        self.origin["model_card"] = {"url":self.origin["artifact_url"].split("gpu-eval")[0]+"README.md",
                                     "payload":{"path": str(card), "sha256":hashlib.sha256(card.read_bytes()).hexdigest()}}
        self.origin["artifact_url"] = "https://raw.githubusercontent.com/other/eval/" + "b"*40 + "/index.json"
        self.assertIsNone(qualify(self.task, self.origin, self.evidence())["baseline"])

    def test_random_fewshot_requires_selected_examples_or_selection_seed(self):
        self.proto["prompt_config"].update(few_shot_num=5, few_shot_random=True)
        self.assertIsNone(qualify(self.task, self.origin, self.evidence())["baseline"])
        self.static["reader_sha256_by_subset"] = {"a":"1"*64}
        self.assertEqual([], qualify(self.task, self.origin, self.evidence())["missing"])

    def test_multirun_report_cannot_supply_single_run_scores(self):
        result = qualify(self.task, self.origin, self.evidence(repeats=3))
        self.assertIsNone(result["baseline"])

    def test_null_sdk_generation_defaults_accept_the_frozen_effective_budget(self):
        self.proto["generation_config"].update(max_tokens=None, top_p=None, stop_seqs=[])
        self.static["effective_max_tokens"] = 128
        result = qualify(self.task, self.origin, self.evidence())
        self.assertEqual([], result["missing"])
        self.assertNotIn("max_tokens", result["baseline"]["generation_config"])

    def test_complete_public_source_through_download_relocation_and_actual_inspection(self):
        from customer_workflow.quality.baseline_search import discover
        original = self.evidence(published=[{"metric":"acc", "gpu_score":0, "target":{"max_drop":0.125}}])
        published_source = json.loads(Path(original["path"]).read_text())["source"]
        prefix = self.origin["artifact_url"].split("gpu-eval")[0]
        names = ["gpu-eval/" + n for n in ("index.json", "gpu.json", "report.json", "config.json")]
        api = "https://huggingface.co/api/models/org/model"
        published = {api: json.dumps({"id":"org/model", "sha":self.origin["revision"],
                            "siblings":[{"rfilename":name} for name in ["README.md", *names]]}).encode(),
                     prefix + "README.md": b"Official evaluation results"}
        for name in names:
            published[prefix + name] = (self.root / Path(name).name).read_bytes()
        with patch("customer_workflow.quality.baseline_search.transport.fetch", side_effect=lambda url, timeout: published[url]):
            result = discover(self.task, {"deployment_receipt":self.origin["deployment_receipt"]}, self.root / "discovery")
        self.assertEqual("found", result["status"], result["missing"])
        self.assertEqual(0, result["baselines"][0]["baseline"]["scores"][0]["gpu_score"])
        self.assertEqual({"max_drop":0.125}, result["baselines"][0]["baseline"]["scores"][0]["target"])
        self.assertEqual(published_source["sha256"], result["baselines"][0]["published_targets"][0]["source"]["sha256"])
