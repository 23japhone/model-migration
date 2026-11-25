"""在客户的同步 GPU worker 内按冻结清单采集一个真实请求。"""
from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import monotonic
from uuid import uuid4

from ..runtime.capture.adapters.alignment.gate import TargetGate, replace
from ..runtime.capture.adapters.alignment.plan import is_target, read_plan
from .record import publish
from .runtime import check_observer, initialize, runtime_identity


def select_frame(runner, internal, plan, count, ids, positions, logits_indices):
    if list(runner.input_batch.req_ids) != [internal]:
        raise ValueError('GPU 实际批次须恰含已绑定的单题请求')
    req = runner.requests[internal]
    if getattr(req, 'mm_features', None):
        raise ValueError('GPU 三遍只支持纯文本')
    if len(req.output_token_ids) != plan['position'] - 1:
        return None
    if ids is None or positions is None or ids.ndim != 1 or positions.ndim != 1:
        raise ValueError('GPU 缺少文本输入行或一维位置')
    frame = {'output_index': len(req.output_token_ids), 'positions': positions[:count].tolist(),
             'input_ids': ids[:count].tolist(), 'logits_indices': logits_indices.tolist()}
    return frame if is_target(plan, frame, req.prompt_token_ids, req.output_token_ids) else None


def check_runner(runner):
    parallel = runner.vllm_config.parallel_config
    if (str(runner.device).partition(':')[0] != 'cuda' or not runner.model_config.enforce_eager
            or runner.use_async_scheduling or runner.speculative_config
            or any(getattr(parallel, key) != 1 for key in ('data_parallel_size', 'tensor_parallel_size',
                                                           'pipeline_parallel_size'))):
        raise ValueError('运行时 GPU 采集需要单卡、同步 Eager 且关闭推测解码')


@contextmanager
def capture_request(runner, plan_ref, directory, *, case, model_files, trace_id, internal_id):
    """在已经绑定到原生请求的 vLLM GPUModelRunner 实例内使用。"""
    check_runner(runner)
    _, plan = read_plan(plan_ref['path'], plan_ref['sha256'], 'gpu')
    if case != plan['case'] or not trace_id or not internal_id:
        raise ValueError('实际模型、题号和请求身份与 GPU 清单不符')
    versions = runtime_identity(runner, plan, model_files)
    folder = Path(directory)
    if not folder.is_absolute() or folder.resolve() != folder:
        raise ValueError('GPU 采集目录须为规范绝对路径')
    folder.mkdir(parents=True, exist_ok=False)
    capture = Capture(runner, folder, plan, plan_ref, trace_id, internal_id)
    capture.versions = versions
    capture.debugger = initialize(runner, folder)
    with ExitStack() as stack:
        for name, wrapper in (('execute_model', capture.execute), ('_prepare_inputs', capture.prepare),
                              ('_model_forward', capture.forward), ('sample_tokens', capture.sample)):
            replace(stack, runner, name, wrapper(getattr(runner, name)))
        replace(stack, runner.sampler, 'greedy_sample', capture.greedy(runner.sampler.greedy_sample))
        yield folder / 'tensor-bundle.json'
        if not capture.done:
            raise ValueError('GPU 重放未取得目标位置的真实前向和接受 Token')


class Capture:
    def __init__(self, runner, folder, plan, source, trace, internal):
        self.runner, self.folder, self.plan, self.source = runner, folder, plan, source
        self.trace, self.internal = trace, internal
        self.count, self.frame, self.rows, self.raw, self.probs = 0, None, None, None, None
        self.done = False
        self.started = None

    def execute(self, original):
        def execute(scheduler_output, *args, **kwargs):
            self.count = scheduler_output.num_scheduled_tokens.get(self.internal, 0)
            if self.count and len(scheduler_output.num_scheduled_tokens) != 1:
                raise ValueError('目标 GPU 前向混入了其他请求')
            self.rows = None
            with ExitStack() as self.probe:
                result = original(scheduler_output, *args, **kwargs)
            if self.frame:
                self.capture_seconds = monotonic() - self.started
                logits = self.runner.execute_model_state.logits
                self.raw = logits.detach().to(device='cpu', copy=True)
            return result
        return execute

    def prepare(self, original):
        def prepare(*args, **kwargs):
            result = original(*args, **kwargs)
            self.rows = result[0]
            return result
        return prepare

    def forward(self, original):
        def forward(input_ids=None, positions=None, **kwargs):
            if self.count and not self.done:
                frame = select_frame(self.runner, self.internal, self.plan, self.count, input_ids, positions, self.rows)
                if frame is not None:
                    self.open(frame)
            return original(input_ids=input_ids, positions=positions, **kwargs)
        return forward

    def open(self, frame):
        if self.frame is not None:
            raise ValueError('一个输出位置对应多个 GPU 前向')
        self.started = monotonic()
        self.frame, self.forward_id = frame, uuid4().hex
        self.gate = TargetGate(self.plan, self.folder, hardware='gpu')
        debugger = self.debugger
        check_observer(debugger)
        collector = debugger.service.data_collector
        self.probe.enter_context(self.gate.intercept(collector))
        self.probe.callback(debugger.step)
        self.probe.callback(debugger.stop)
        debugger.start(model=self.runner.get_model(), rank_id=0)
        if debugger.service.data_collector is not collector or debugger.service.config.async_dump is not False:
            raise ValueError('启动时 msProbe 的原始采集器或同步写盘状态改变')

    def greedy(self, original):
        def greedy(logits):
            result = original(logits)
            if self.frame:
                import torch
                if not self.runner.input_batch.sampling_metadata.all_greedy:
                    raise ValueError('GPU worker 的实际采样运行没有全部使用贪心模式')
                self.probs = torch.softmax(logits.detach().to(device='cpu', dtype=torch.float64), dim=-1)
                self.candidate = int(result.item())
            return result
        return greedy

    def sample(self, original):
        def sample(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.frame:
                ids = result.sampled_token_ids[result.req_id_to_index[self.internal]]
                if len(ids) != 1 or self.probs is None or self.candidate != ids[0]:
                    raise ValueError('GPU 采样没有复核到唯一的贪心接受 ID')
                publish(self, ids[0])
                self.done, self.frame = True, None
            return result
        return sample
