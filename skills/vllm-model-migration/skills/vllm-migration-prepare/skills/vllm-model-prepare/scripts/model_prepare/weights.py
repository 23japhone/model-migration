"""核验权重索引及 Safetensors 头部，不加载张量。"""
import json
from pathlib import Path
import struct

from .inventory import checked_file
from .request import relative_file


def required_file(root, name):
    path = checked_file(root, name)
    if path.stat().st_size == 0:
        raise ValueError(f"模型文件为空：{name}")
    with path.open("rb") as stream:
        if stream.read(80).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise ValueError(f"Git LFS 指针尚未下载为实际文件：{name}")
    return path


def read_object(root, name):
    try:
        value = json.loads(required_file(root, name).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"模型 JSON 文件无效：{name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"模型 JSON 必须为对象：{name}")
    return value


def safetensor_keys(root, name):
    path = required_file(root, name)
    size = path.stat().st_size
    with path.open("rb") as stream:
        encoded_length = stream.read(8)
        if len(encoded_length) != 8:
            raise ValueError(f"Safetensors 头部无效：{name}")
        length = struct.unpack("<Q", encoded_length)[0]
        if not 2 <= length <= min(size - 8, 100 * 1024 * 1024):
            raise ValueError(f"Safetensors 头部长度无效：{name}")
        try:
            header = json.loads(stream.read(length))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Safetensors JSON 无效：{name}") from error
    if not isinstance(header, dict):
        raise ValueError(f"Safetensors 头部必须为对象：{name}")
    offsets = []
    keys = set()
    for key, tensor in header.items():
        if key != "__metadata__":
            offsets.append(validate_tensor(tensor, size - 8 - length, name))
            keys.add(key)
    position = 0
    for start, end in sorted(offsets):
        if start != position:
            raise ValueError(f"Safetensors 数据存在间隙或重叠：{name}")
        position = end
    if not keys or position != size - 8 - length:
        raise ValueError(f"Safetensors 数据区大小不一致：{name}")
    return keys


def validate_tensor(tensor, payload_size, name):
    if not isinstance(tensor, dict) or not isinstance(tensor.get("dtype"), str):
        raise ValueError(f"Safetensors 张量元数据无效：{name}")
    shape = tensor.get("shape")
    offsets = tensor.get("data_offsets")
    if not isinstance(shape, list) or any(type(item) is not int or item < 0 for item in shape):
        raise ValueError(f"Safetensors 张量形状无效：{name}")
    if not isinstance(offsets, list) or len(offsets) != 2 or any(type(item) is not int for item in offsets):
        raise ValueError(f"Safetensors 数据偏移无效：{name}")
    start, end = offsets
    if not 0 <= start <= end <= payload_size:
        raise ValueError(f"Safetensors 数据超出文件范围：{name}")
    byte_width = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4,
                  "I16": 2, "I8": 1, "U64": 8, "U32": 4, "U16": 2, "U8": 1, "BOOL": 1,
                  "F8_E4M3": 1, "F8_E5M2": 1}.get(tensor["dtype"])
    elements = 1
    for dimension in shape:
        elements *= dimension
    if byte_width is None:
        raise ValueError(f"Safetensors 不支持该 dtype：{tensor['dtype']}，文件：{name}")
    if elements * byte_width != end - start:
        raise ValueError(f"Safetensors 张量形状与大小不一致：{name}")
    return start, end


def index_tensors(root, indexes):
    tensors = {}
    weights = set()
    for name in indexes:
        suffix = ".safetensors" if name.endswith(".safetensors.index.json") else ".bin"
        mapping = read_object(root, name).get("weight_map")
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError(f"权重索引必须包含非空的 weight_map：{name}")
        for tensor, shard in mapping.items():
            if not isinstance(tensor, str) or not tensor or not isinstance(shard, str):
                raise ValueError(f"weight_map 条目无效：{name}")
            relative_file(shard)
            if not shard.endswith(suffix):
                raise ValueError(f"权重分片必须使用 {suffix}: {shard}")
            required_file(root, shard)
            weights.add(shard)
            tensors.setdefault(shard, set()).add(tensor)
    return weights, tensors


def validate_weights(root, names):
    indexes = [name for name in names if name.endswith((".safetensors.index.json", ".bin.index.json"))]
    weights, tensors = index_tensors(root, indexes)
    if not indexes:
        weights = {name for name in ("model.safetensors", "pytorch_model.bin") if name in names}
    if not weights:
        raise ValueError("模型必须包含权重索引，或 model.safetensors/pytorch_model.bin")
    for name in sorted(weights):
        required_file(root, name)
        if name.endswith(".safetensors"):
            keys = safetensor_keys(root, name)
            missing = tensors.get(name, set()) - keys
            if missing:
                raise ValueError(f"权重索引中的张量在分片中缺失，文件 {name}: {sorted(missing)[:5]}")
    return {"files": sorted(weights), "indexes": indexes,
            "binary_structure": "not_evaluated" if any(not name.endswith(".safetensors") for name in weights) else "pass"}
