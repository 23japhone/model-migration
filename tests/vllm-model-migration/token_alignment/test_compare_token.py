"""从公开 CLI 验证固定合成样例、覆盖边界、来源及 CPU 单点核验。"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "skills/vllm-model-migration/skills/vllm-precision-align/skills/vllm-precision-compare"
FIXTURE = json.loads((HERE / "fixtures/first-anomaly.json").read_text())


def patched(patches):
    row = copy.deepcopy(FIXTURE["gpu_record"])
    for edit in patches:
        assert edit["op"] == "replace"
        target = row
        keys = edit["path"].lstrip("/").split("/")
        for key in keys[:-1]:
            target = target[int(key) if isinstance(target, list) else key]
        key = int(keys[-1]) if isinstance(target, list) else keys[-1]
        target[key] = copy.deepcopy(edit["value"])
    return row


class TokenComparison(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def write(self, filename, row):
        path = self.root / filename
        data = json.dumps(row, ensure_ascii=False).encode()
        path.write_bytes(data)
        return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}

    def request(self, gpu, npu, cpu=None):
        req = {**FIXTURE["task_json_accuracy_fragment"], "gpu": self.write("gpu.json", gpu),
               "npu": self.write("npu.json", npu)}
        if cpu is not None:
            req["cpu"] = self.write("cpu.json", cpu)
        return req

    def invoke(self, req, *extra):
        out = self.root / "report.json"
        out.unlink(missing_ok=True)
        request = self.write("request.json", req)
        proc = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/compare.py"), "--action", "token",
                               "--request", request["path"], "--output", str(out), *extra],
                              text=True, capture_output=True, check=False)
        if proc.returncode:
            return proc.returncode, proc.stderr
        self.assertEqual(out.exists(), "--plan" not in extra)
        if out.exists():
            self.assertEqual(proc.stdout, out.read_text())
        return 0, json.loads(proc.stdout)

    def compare(self, npu, *, gpu=None, cpu=None):
        baseline = gpu if gpu is not None else patched([])
        exit_code, result = self.invoke(self.request(baseline, npu, cpu))
        self.assertEqual(exit_code, 0, result)
        return result

    def test_reference_cases(self):
        for case in FIXTURE["test_cases"]:
            with self.subTest(name=case["name"]):
                npu = patched(case["npu_patch"])
                npu["evidence_kind"] = "synthetic"
                report = self.compare(npu, gpu=dict(patched([]), evidence_kind="synthetic"))
                self.assertEqual(report["status"], "completed")
                for field, value in case["expected"].items():
                    self.assertEqual(report[field], value)
                self.assertTrue(report["reason"])
                self.assertEqual(report["evidence_kind"]["gpu"], "synthetic")
                self.assertEqual(report["evidence"]["npu"]["path"], str(self.root / "npu.json"))

    def oracle(self):
        # 第 2 步 CPU 权重的解析解，独立于 NPU 注入的误差。
        return {"sample_id": "toy-001", "prompt_token_ids": [0, 1], "prefix_token_ids": [0, 1, 1],
                "position": 2, "raw_logits": [1.6094379124341003, 0, 0, 0],
                "processed_probs": [0.625, 0.125, 0.125, 0.125], "evidence_kind": "analytical_synthetic"}

    def test_legacy_cpu_is_ignored_even_if_its_file_disappears(self):
        npu = patched(FIXTURE["test_cases"][3]["npu_patch"])
        req = self.request(patched([]), npu, self.oracle())
        Path(req["cpu"]["path"]).unlink()
        code, report = self.invoke(req)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["result"], "mismatch")
        self.assertEqual(report["validity"], "valid")
        self.assertEqual(report["cpu_check"]["status"], "unavailable")
        self.assertEqual(report["cpu_check"]["position"], 2)
        self.assertEqual(report["cpu_check"]["attempts"], [])
        self.assertIsNone(report["cpu_check"]["gpu"])
        self.assertNotIn("cpu", report["evidence"])

    def test_plan_validates_shape_without_opening_sources(self):
        req = self.request(patched([]), patched([]))
        Path(req["npu"]["path"]).unlink()
        code, report = self.invoke(req, "--plan")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "not_run")
        req["token_alignment"] = {"raw_logits": {"atol": False}, "processed_scores": {"atol": 0}}
        self.assertEqual(self.invoke(req, "--plan")[0], 2)

    def test_replaced_file_rejected(self):
        req = self.request(patched([]), patched([]))
        req["gpu"]["sha256"] = "0" * 64
        self.assertEqual(self.invoke(req)[0], 2)

    def test_bad_vector_cannot_pass(self):
        for key, bad in [("raw_logits", [0, 1]), ("processed_probs", [1.1, 0, 0, 0]),
                         ("raw_logits", [True, 1, 1, 1])]:
            with self.subTest(key=key, value=bad):
                npu = patched([])
                npu[key][1] = bad
                report = self.compare(npu)
                self.assertEqual(report["result"], "insufficient_evidence")
                self.assertEqual(report["first_missing_token"], 2)
                self.assertEqual(report["covered_tokens"], 1)

    def test_policy_and_evidence_are_distinct(self):
        npu = patched([])
        npu["sample_id"] = "other"
        self.assertEqual(self.compare(npu)["result"], "insufficient_evidence")
        req = self.request(patched([]), patched([]))
        req["token_alignment"] = {"raw_logits": {"atol": -1}, "processed_scores": {"atol": 1}}
        self.assertEqual(self.invoke(req)[0], 2)

    def test_single_step_files_and_first_suspect_short_circuit(self):
        import struct
        gpu = patched([])
        npu = patched(FIXTURE["test_cases"][2]["npu_patch"])
        for i, kind in enumerate(("bfloat16", "float16", "float32", "float64")):
            gpu = patched([])
            npu = patched(FIXTURE["test_cases"][2]["npu_patch"])
            data = self.root / f"probs-{i}.bin"
            probs = [0.125, 0.625, 0.125, 0.125]
            if kind == "bfloat16":
                payload = b"".join(struct.pack("<f", p)[2:] for p in probs)
            else:
                fmt = {"float16": "e", "float32": "f", "float64": "d"}[kind]
                payload = struct.pack("<4" + fmt, *probs)
            data.write_bytes(payload)
            ref = {"path": data.name, "sha256": hashlib.sha256(payload).hexdigest(), "size": 4, "dtype": kind}
            for row in (gpu, npu):
                row["processed_probs"][0] = ref
                row["raw_logits"][1] = {"path": "not-read.bin"}
            result = self.compare(npu, gpu=gpu)
            self.assertEqual(result["result"], "mismatch")
            self.assertEqual(result["first_anomalous_token"], 1)
            self.assertEqual(result["covered_tokens"], 1)
            self.assertEqual(result["differences"]["raw_logits"]["max_abs_error"], 0.25)
            npu["processed_probs"][0] = dict(ref, sha256="0"*64)
            self.assertEqual(self.compare(npu, gpu=gpu)["result"], "insufficient_evidence")
            npu["processed_probs"][0] = dict(ref, path="../" + data.name)
            self.assertEqual(self.compare(npu, gpu=gpu)["first_missing_token"], 1)

    def test_missing_input_scope_and_probabilities(self):
        mismatched = self.compare(dict(patched([]), prompt_token_ids=[0]))
        self.assertEqual(mismatched["first_different_input_token"], 2)
        npu = patched([])
        npu["processed_probs"][0] = [0.1] * 4
        self.assertEqual(self.compare(npu)["result"], "insufficient_evidence")
        npu = patched([])
        npu["prompt_token_ids"][1] = 5
        self.assertEqual(self.compare(npu, gpu=copy.deepcopy(npu))["result"], "insufficient_evidence")

    def test_real_cpu_forward_is_bound_and_runs_only_on_suspect(self):
        import importlib.util
        if not all(importlib.util.find_spec(name) for name in ("torch", "transformers", "safetensors")):
            self.skipTest("真实 CPU 前向需要已安装 torch/transformers/safetensors")
        import torch
        from transformers import GPT2Config, GPT2LMHeadModel
        model_dir = self.root / "model"
        config = GPT2Config(n_embd=8, n_layer=1, n_head=1, n_positions=8, vocab_size=4)
        model = GPT2LMHeadModel(config).cpu()
        for param in model.parameters():
            torch.nn.init.zeros_(param)
        model.save_pretrained(model_dir, safe_serialization=True)
        # 发行方可能同时归档 Meta 原版权重；本地 HF 在 use_safetensors 模式下仅加载标准文件。
        original = model_dir / "original" / "consolidated.00.pth"
        original.parent.mkdir()
        original.write_bytes(b"vendor-checkpoint-alternative")
        files = {f.relative_to(model_dir).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                 for f in model_dir.rglob("*") if f.is_file()}
        identity = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        spec = {"root": str(model_dir), "model_identity": identity, "files": files,
                "weight_files": ["model.safetensors", "original/consolidated.00.pth"], "plain_greedy": True}
        req = self.request(patched([]), patched(FIXTURE["test_cases"][3]["npu_patch"]), self.oracle())
        req["cpu_model"] = spec
        code, result = self.invoke(req)
        self.assertEqual(code, 0, result)
        check = result["cpu_check"]
        self.assertEqual(check["status"], "completed", check)
        self.assertEqual(check["model_identity"], identity)
        self.assertEqual(check["device"], "cpu")
        self.assertEqual(check["dtype"], "float32")
        self.assertEqual(check["attempts"], [{"dtype": "float32", "status": "completed"}])
        self.assertEqual(check["position"], 2)
        self.assertEqual(check["gpu"]["raw_logits"]["max_abs_error"], 1.6094379124341003)
        self.assertEqual(check["gpu"]["processed_scores"]["max_abs_error"], 0.375)
        spec["plain_greedy"] = False
        _, result = self.invoke(req)
        self.assertEqual(result["cpu_check"]["gpu"]["processed_scores"]["status"], "unavailable")
        self.assertIn("max_abs_error", result["cpu_check"]["gpu"]["raw_logits"])
        (model_dir / "model.safetensors").write_bytes(b"replaced")
        _, result = self.invoke(req)
        self.assertEqual(result["result"], "mismatch")
        self.assertEqual(result["cpu_check"]["status"], "unavailable")
        req["npu"] = req["gpu"]
        _, result = self.invoke(req)
        self.assertEqual(result["cpu_check"]["status"], "not_applicable")

    def test_explicit_case_matches_optional_inline_subset_but_rejects_conflicts(self):
        req = self.request(dict(patched([]), subset="toy"), patched([]))
        req["case"] = {"subset": "toy", "sample_id": "toy-001"}
        code, report = self.invoke(req)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["result"], "aligned")
        self.assertEqual(report["subset"], "toy")
        req["case"]["subset"] = "wrong"
        self.assertEqual(self.invoke(req)[1]["result"], "insufficient_evidence")
        req["case"] = {"subset": "toy", "sample_id": "other"}
        self.assertEqual(self.invoke(req)[1]["result"], "insufficient_evidence")

    def test_unknown_stop_cannot_prove_same_termination(self):
        gpu = dict(patched([]), stop_reason="stop")
        npu = dict(patched([]), stop_reason="unknown_stop")
        for other in (gpu, npu):
            self.assertEqual(self.compare(npu, gpu=other)["result"], "insufficient_evidence")
        self.assertEqual(self.compare(dict(npu, stop_reason="stop_string:###"), gpu=gpu)["result"],
                         "insufficient_evidence")

    def test_official_snapshot_blobs_are_valid_model_reference_files(self):
        # 原生数据准备允许官方快照链接到同仓 blobs。CPU 参考须接受相同文件边界。
        import importlib
        scripts = str(SKILL / "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        cpu = importlib.import_module("comparison.token_cpu")
        repo = self.root / "models--fixture--toy"
        root = repo / "snapshots" / ("f"*40)
        root.mkdir(parents=True)
        blobs = repo / "blobs"
        blobs.mkdir()
        files = {}
        for name, content in (("config.json", b"{}"), ("model.safetensors", b"test")):
            digest = hashlib.sha256(content).hexdigest()
            (blobs / digest).write_bytes(content)
            (root / name).symlink_to(blobs / digest)
            files[name] = digest
        identity = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        spec = {"root": str(root), "files": files, "weight_files": ["model.safetensors"], "model_identity": identity}
        self.assertEqual(cpu.verify_files(spec), root)
        (root / "config.json").unlink()
        bad = repo / "outside.json"
        bad.write_bytes(b"{}")
        (root / "config.json").symlink_to(bad)
        with self.assertRaises(ValueError):
            cpu.verify_files(spec)

    def test_early_stop_needs_an_actual_extra_token(self):
        npu = patched(FIXTURE["test_cases"][7]["npu_patch"])
        gpu = patched([])
        gpu["output_token_ids"][2] = None
        report = self.compare(npu, gpu=gpu)
        self.assertEqual(report["result"], "insufficient_evidence")
        self.assertEqual(report["first_missing_token"], 3)
