"""读取单个生成位置的全词表向量，核验位置、字节和概率语义。"""
from decimal import Decimal, Inexact, localcontext
import hashlib
from pathlib import Path
import struct

from .samples import numeric

ENCODINGS = {"float64": ("<d", 8), "float32": ("<f", 4), "float16": ("<e", 2), "bfloat16": ("<H", 2)}


def source_path(ref, directory):
    if set(ref) != {"path", "sha256", "dtype", "size"} or not isinstance(ref["path"], str):
        raise ValueError("单步向量引用字段无效")
    relative = Path(ref["path"])
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("单步向量须使用记录目录内的相对路径")
    path = directory / relative
    if path.resolve() != path or not path.is_file():
        raise ValueError("单步向量文件缺失或重定向")
    return path


def binary_vector(ref, directory):
    path = source_path(ref, directory)
    fmt, width = ENCODINGS[ref["dtype"]]
    size = ref["size"]
    if type(size) is not int or size < 1 or size * width != path.stat().st_size:
        raise ValueError("向量词表长度与文件字节数不符")
    data = path.read_bytes()
    if len(data) != size * width or hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("向量字节或摘要不符")
    values = (entry[0] for entry in struct.iter_unpack(fmt, data))
    if ref["dtype"] == "bfloat16":
        values = (struct.unpack("<f", struct.pack("<I", value << 16))[0] for value in values)
    # 精确展开二进制浮点，避免额外降精度。
    return [Decimal.from_float(value) for value in values]


def check_probabilities(values, dtype):
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("处理后概率超出 [0,1]")
    # 这里只容许概率本身归一化产生的舍入误差，逐候选的双端判定按客户 atol 执行。
    tolerance = Decimal("0.005") if dtype == "bfloat16" else Decimal("0.001")
    if abs(sum(values) - 1) > tolerance:
        raise ValueError("处理后概率未归一")


def vector(source, directory, *, probabilities=False):
    if isinstance(source, dict):
        values = binary_vector(source, directory)
    elif isinstance(source, list) and source:
        values = [numeric(value) for value in source]
    else:
        raise ValueError("该位置缺少全词表向量")
    if not all(value.is_finite() for value in values):
        raise ValueError("全词表向量含非有限数")
    if probabilities:
        check_probabilities(values, source.get("dtype") if isinstance(source, dict) else None)
    return values


def step_vectors(raw, probs, directory):
    scores = vector(raw, directory)
    probabilities = vector(probs, directory, probabilities=True)
    if len(scores) != len(probabilities):
        raise ValueError("原始分数与处理后概率的词表长度不同")
    return scores, probabilities


def read_step(artifact, index):
    row = artifact.value
    return step_vectors(row["raw_logits"][index], row["processed_probs"][index], Path(artifact.ref["path"]).parent)


def peak_error(left, right):
    if len(left) != len(right):
        raise ValueError("两份向量的词表长度不同")
    # 2048 位足以精确相减任意两个 IEEE binary64 有限值，包括极端指数和次正规数。
    with localcontext() as context:
        context.prec = 2048
        context.traps[Inexact] = True
        return max(enumerate(abs(x - y) for x, y in zip(left, right)), key=lambda item: item[1])
