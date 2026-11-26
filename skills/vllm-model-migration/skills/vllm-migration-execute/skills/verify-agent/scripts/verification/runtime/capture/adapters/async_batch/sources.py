"""目标镜像内实际使用的异步队列、调度器、结果包装及执行器。"""
import hashlib
import inspect
from pathlib import Path

CORE = 'vllm.v1.engine.core'
DIGESTS = {
    CORE: 'e1486de709675fa64109bb79f2b35b19c974b38a8bc2616b5e177ea3ff96c421',
    'vllm.v1.core.sched.async_scheduler': '4df37e8842fb06d5cce3bc414e9423a09808e9336a96a25530dd2270f078d093',
    'vllm.v1.worker.gpu_model_runner': 'd71f4eb4547717a26a893d46d1dcb877731f9f5cba1de2f65376d24a6f95221f',
    'vllm.v1.executor.uniproc_executor': 'b4783f43e65ac2a6b0c4faa93e5d65a18f93a8a21fe9d886c221bd0b39811c6a',
    'vllm.v1.executor.multiproc_executor': '2cca69138a2fdfe0c08cc3b3767964f63ae24e3f5bfbb2ac6826fa70c295458f',
}


def verify(cls):
    name = cls.__module__
    digest = hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest()
    if digest != DIGESTS.get(name):
        raise ValueError(f'异步目标实现未经核实 {name} {digest}')
    return name


def requested(command):
    flags = {arg.replace('_', '-') for arg in command}
    if '--async-scheduling' not in flags:
        return False
    if '--no-async-scheduling' in flags:
        raise ValueError('同时声明了同步与异步调度')
    return True


def runner_mode(runner, parallel):
    from .. import state
    asynchronous = (state.root() / 'async-batch.json').is_file()
    if (not runner.model_config.enforce_eager or runner.use_async_scheduling != asynchronous
            or runner.speculative_config or parallel.data_parallel_size != 1 or runner.debugger is not None):
        raise ValueError('实际 worker 与 Eager 采集部署声明不符')
    if asynchronous:
        from vllm.v1.worker.gpu_model_runner import AsyncGPUModelRunnerOutput
        verify(AsyncGPUModelRunnerOutput)
    return asynchronous
