"""核验请求所需模型资产，并生成独立的资产清单。"""
from pathlib import Path
import math

from .inventory import checked_file, content_identity, enumerate_files, file_state, scan, verify_record
from .request import is_metadata
from .weights import read_object, required_file, validate_weights


TOKENIZERS = {"tokenizer.json", "tokenizer.model", "spiece.model", "sentencepiece.bpe.model", "vocab.txt", "tekken.json"}


def nonempty_template(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value.values())
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, dict) and isinstance(item.get("template"), str)
                                   and item["template"].strip() for item in value)
    return False


def validate_tokenizer(root, names, request):
    files = TOKENIZERS.intersection(names)
    if {"vocab.json", "merges.txt"} <= set(names):
        files |= {"vocab.json", "merges.txt"}
    if not files:
        raise ValueError("模型 tokenizer 资产缺失，或采用了未支持的自定义格式")
    for name in sorted(files):
        if name.endswith(".json"):
            value = read_object(root, name)
            if name == "tokenizer.json":
                validate_fast_tokenizer(value)
        else:
            required_file(root, name)
    if request["task"] == "chat":
        validate_chat_template(root, names)


def validate_fast_tokenizer(value):
    tokenizer = value.get("model")
    if not isinstance(tokenizer, dict) or tokenizer.get("type") not in ("BPE", "WordPiece", "WordLevel", "Unigram"):
        raise ValueError("tokenizer.json 必须指定支持的 tokenizer 类型（BPE/WordPiece/WordLevel/Unigram）")
    vocab = tokenizer.get("vocab")
    if tokenizer["type"] == "Unigram":
        validate_unigram_vocab(vocab)
        return
    if not isinstance(vocab, dict) or not vocab or any(type(item) is not int or item < 0 for item in vocab.values()):
        raise ValueError("tokenizer 必须包含非空 vocab，token ID须为非负整数")
    if tokenizer["type"] == "BPE":
        validate_bpe_merges(tokenizer.get("merges"))


def validate_unigram_vocab(vocab):
    if not isinstance(vocab, list) or not vocab:
        raise ValueError("Unigram tokenizer 必须包含非空 vocab 列表")
    for item in vocab:
        if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
            raise ValueError("Unigram vocab 项必须包含字符串 token 和数值分数")
        if type(item[1]) not in (int, float) or not math.isfinite(item[1]):
            raise ValueError("Unigram vocab 分数必须为有限数值")


def validate_bpe_merges(merges):
    if not isinstance(merges, list):
        raise ValueError("BPE tokenizer 必须包含 merges 列表")
    for item in merges:
        pair = item.split(" ") if isinstance(item, str) else item
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(part, str) or not part for part in pair):
            raise ValueError("BPE merges 项必须包含两个非空字符串 token")


def validate_chat_template(root, names):
    for name in ("tokenizer_config.json", "processor_config.json", "chat_template.json"):
        if name in names and nonempty_template(read_object(root, name).get("chat_template")):
            return
    for name in names:
        if name == "chat_template.jinja" or (name.startswith("additional_chat_templates/") and name.endswith(".jinja")):
            if required_file(root, name).read_text(encoding="utf-8").strip():
                return
    raise ValueError("chat 任务必须提供非空聊天模板；其他任务请按实际用途指定")


def validate_processors(root, names, modalities):
    if set(modalities) - {"text"}:
        processors = {"processor_config.json", "preprocessor_config.json", "video_preprocessor_config.json"} & set(names)
        if not processors:
            raise ValueError("请求的多模态资产必须包含 processor 或 preprocessor 配置")
        for name in sorted(processors):
            if not read_object(root, name):
                raise ValueError(f"processor 配置为空：{name}")


def validate_config(root):
    config = read_object(root, "config.json")
    architectures = config.get("architectures")
    if architectures is not None and (not isinstance(architectures, list) or not architectures or any(
            not isinstance(item, str) or not item.strip() for item in architectures)):
        raise ValueError("config.json 的 architectures 必须包含非空字符串")
    model_type = config.get("model_type")
    if not architectures and (not isinstance(model_type, str) or not model_type.strip()):
        raise ValueError("config.json 必须包含 architectures 或 model_type，供后续支持度预检使用")
    return config


def inventory_inputs(root, request, source):
    available = enumerate_files(root)
    names = available if request["scope"] == "full" else [name for name in available if is_metadata(name)]
    names = sorted(set(names) | set(request["required_files"]))
    expectations = {row["path"]: row for row in (source or {}).get("source_files", [])}
    if source:
        names = sorted(set(source["selected_files"]) | set(request["required_files"]))
    return names, expectations


def inspect_assets(request, source=None):
    root = Path(request["path"]).resolve()
    available_before = enumerate_files(root)
    names, expectations = inventory_inputs(root, request, source)
    before = {name: file_state(checked_file(root, name)) for name in names}
    files = scan(root, names, expectations)
    for row in files:
        verify_record(row, request["expected_files"].get(row["path"], {}), row["path"])
    config, weights = validate_requested_assets(root, request, names)
    identity = content_identity(files)
    if request.get("expected_identity") is not None and identity != request["expected_identity"]:
        raise ValueError("模型内容身份不一致")
    if before != {name: file_state(checked_file(root, name)) for name in names} or available_before != enumerate_files(root):
        raise ValueError("模型文件在核验期间变化，未记录稳定的内容身份")
    return {"path": str(root), "scope": request["scope"], "task": request.get("task"),
            "modalities": request["modalities"], "files": files, "identity": identity, "weights": weights,
            "metadata": {"architectures": config.get("architectures"), "model_type": config.get("model_type"),
                         "config": config},
            "provenance": source or {"upstream_status": "unverified", "kind": "local_content"}}


def validate_requested_assets(root, request, names):
    config = validate_config(root)
    for name in names:
        if is_metadata(name) and name.endswith(".json"):
            read_object(root, name)
    weights = {"files": [], "indexes": [], "binary_structure": "not_evaluated"}
    if request["scope"] == "full":
        validate_tokenizer(root, names, request)
        validate_processors(root, names, request["modalities"])
        weights = validate_weights(root, names)
    return config, weights
