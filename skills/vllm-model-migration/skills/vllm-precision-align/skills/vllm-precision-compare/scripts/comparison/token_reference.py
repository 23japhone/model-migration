"""在 GPU/NPU 首个双端预测差异处运行 CPU 前向，独立报告两端偏差。"""
from contextlib import redirect_stdout
import multiprocessing
import sys
from time import monotonic

from .evidence import bound
from .token_cpu import forward
from .token_vectors import peak_error, read_step


def measure(device, oracle, tolerances):
    facts = {}
    for field, values, ref in zip(("raw_logits", "processed_scores"), device, oracle):
        if ref is None:
            facts[field] = {"status": "unavailable", "reason": "CPU 没有同一选词规则的参考概率"}
            continue
        token_id, error = peak_error(values, ref)
        facts[field] = {"token_id": token_id, "max_abs_error": error, "within_tolerance": error <= tolerances[field]}
    return facts


def unavailable(error, attempts):
    reason = str(error) or type(error).__name__
    if attempts and attempts[-1]["status"] == "running":
        attempts[-1].update(status="failed", error=reason)
    return {"status": "unavailable", "reason": reason, "attempts": attempts, "gpu": None, "npu": None}


def cpu_worker(channel, spec, references, prefix, position, tolerances):
    attempts = []

    def started(name):
        channel.send([*attempts, {"dtype": name, "status": "running"}])

    with channel, redirect_stdout(sys.stderr):
        try:
            oracle, metadata = forward(spec, prefix, attempts, started)
            checked = {side: measure(read_step(bound(source), position - 1), oracle, tolerances)
                       for side, source in references.items()}
            report = {"status": "completed", "attempts": attempts, **metadata, **checked}
        except Exception as exc:
            report = unavailable(exc, attempts)
        channel.send(report)


def worker_result(reader, process):
    attempts = []
    deadline = monotonic() + 14400
    while reader.poll(max(0, deadline - monotonic())):
        try:
            message = reader.recv()
        except (EOFError, OSError):
            process.join(1)
            return unavailable(f"CPU 复算进程异常退出，退出码 {process.exitcode}", attempts)
        if isinstance(message, dict):
            return message
        attempts = message
    return unavailable("CPU 复算超过四小时的单题时限", attempts)


def run_worker(spec, devices, prefix, position, tolerances):
    # 子进程拥有模型和 CPU 内核。即使底层加载器被系统终止，父进程仍持有双端比较结果。
    context = multiprocessing.get_context("spawn")
    sources = {side: data.ref for side, data in devices.items()}
    reader, writer = context.Pipe(duplex=False)
    with reader, writer:
        process = context.Process(target=cpu_worker, args=(writer, spec, sources, prefix, position, tolerances))
        process.start()
        writer.close()
        try:
            return worker_result(reader, process)
        finally:
            process.join(1)
            if process.is_alive():
                process.kill()
            process.join()
            process.close()


def verify_cpu(req, devices, report, tolerances):
    if report["result"] != "mismatch":
        return {"status": "not_applicable", "reason": "没有具有双端预测向量的首个可疑位置"}
    position = report["first_anomalous_token"]
    try:
        if "cpu_model" not in req:
            raise ValueError(req.get("cpu_model_error") or "缺少本次 CPU 复算所需的冻结模型权重清单")
        gpu = devices["gpu"].value
        prefix = gpu["prompt_token_ids"] + gpu["output_token_ids"][:position - 1]
        check = run_worker(req["cpu_model"], devices, prefix, position, tolerances)
    except Exception as exc:
        check = unavailable(exc, [])
    if check["status"] == "completed":
        report["evidence_kind"]["cpu"] = "local_cpu_forward"
    return {"position": position, **check}
