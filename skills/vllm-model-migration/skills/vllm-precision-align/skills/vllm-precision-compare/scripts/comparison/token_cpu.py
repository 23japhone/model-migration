"""在已冻结的本地 safetensors 权重上完成首疑点的独立 CPU 前向。"""
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys

from .evidence import invalid_constant, path_of, unique

sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "scripts"))
from model_cache import checked as checked_model_file


def validate_spec(spec):
    required = {"root", "files", "weight_files", "model_identity"}
    if not isinstance(spec, dict) or not required <= spec.keys() or spec.keys() - required - {"dtype", "plain_greedy"}:
        raise ValueError("CPU 模型须提供冻结模型目录、权重清单和协议身份")
    path_of(spec["root"])
    if spec.get("dtype", "float32") not in ("float32", "float64") or type(spec.get("plain_greedy", False)) is not bool:
        raise ValueError("CPU 参考精度或选词规则声明无效")
    if not isinstance(spec["files"], dict) or not isinstance(spec["weight_files"], list) or not spec["weight_files"]:
        raise ValueError("CPU 模型文件清单无效")


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_weight_index(root, files, weights):
    single = "model.safetensors"
    index = "model.safetensors.index.json"
    if (root / "adapter_config.json").exists():
        raise ValueError("CPU 参考暂未核验外部 adapter 的有效权重")
    if (root / single).is_file():
        actual_weights = {single}
    else:
        if index not in files:
            raise ValueError("缺少绑定全部 safetensors 分片的权重索引")
        data = json.loads((root / index).read_bytes(), object_pairs_hook=unique, parse_constant=invalid_constant)
        actual_weights = set(data["weight_map"].values())
    safetensors = {name for name in weights if name.endswith(".safetensors")}
    if actual_weights != safetensors or not set(weights) <= files.keys():
        raise ValueError("CPU 实际加载的权重与原模型权重清单不同")


def verify_files(spec):
    root = path_of(spec["root"])
    files = spec["files"]
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != spec["model_identity"] or "config.json" not in files:
        raise ValueError("CPU 模型文件与首轮冻结的模型身份不同")
    for name, expected in files.items():
        path = checked_model_file(root, name)
        if not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError("非法 CPU 文件清单路径或摘要")
        if file_hash(path) != expected:
            raise ValueError("CPU 模型文件不符合冻结的内容摘要：" + name)
    check_weight_index(root, files, spec["weight_files"])
    return root


def check_model(model, loading):
    if any(loading.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
        raise ValueError("CPU 模型未完整使用已冻结权重")
    for parameter in model.parameters():
        if parameter.device.type != "cpu" or (parameter.is_floating_point() and parameter.element_size() < 4):
            raise ValueError("模型参数的实际设备或精度不满足 CPU 高精度复核要求")


def forward(spec, prefix):
    import torch
    import transformers
    from transformers import AutoConfig, AutoModelForCausalLM

    root = verify_files(spec)
    config = AutoConfig.from_pretrained(root, local_files_only=True, trust_remote_code=False)
    if getattr(config, "quantization_config", None):
        raise ValueError("该量化模型尚未核验 CPU 有效权重")
    default_dtype = "float64" if getattr(config, "torch_dtype", None) == torch.float64 else "float32"
    dtype = getattr(torch, spec.get("dtype", default_dtype))
    model, loading = AutoModelForCausalLM.from_pretrained(
        root, config=config, local_files_only=True, trust_remote_code=False, use_safetensors=True,
        torch_dtype=dtype, output_loading_info=True, attn_implementation="eager")
    check_model(model, loading)
    model.eval()
    inputs = torch.tensor([prefix], device="cpu", dtype=torch.long)
    options = {"logits_to_keep": 1} if "logits_to_keep" in inspect.signature(model.forward).parameters else {}
    with torch.inference_mode():
        scores = model(input_ids=inputs, use_cache=False, **options).logits[0, -1].to(torch.float64)
        if not bool(torch.isfinite(scores).all()):
            raise ValueError("CPU 前向产生非有限分数")
        raw = [Decimal.from_float(value) for value in scores.tolist()]
        probs = ([Decimal.from_float(value) for value in torch.softmax(scores, dim=0).tolist()]
                 if spec.get("plain_greedy") else None)
    return (raw, probs), {"device": "cpu", "dtype": str(dtype).removeprefix("torch."),
                          "model_identity": spec["model_identity"], "torch_version": str(torch.__version__),
                          "transformers_version": transformers.__version__}
