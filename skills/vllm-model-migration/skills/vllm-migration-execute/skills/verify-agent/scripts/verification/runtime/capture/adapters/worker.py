"""在同一同步 runner 的 execute/sample 状态间传递前向身份。"""
from pathlib import Path
import re
import sys
import uuid

from . import state
from .coverage import Coverage


def save_vector(path, tensor, size):
    import torch
    if tensor.ndim != 2 or tensor.shape != (1, size):
        raise ValueError(f'全词表行形状不符 {tensor.shape}，预期 (1,{size})')
    row = tensor[0].detach().cpu().contiguous().clone()
    path.write_bytes(row.view(torch.uint8).numpy().tobytes())
    return {'file': path.name, 'dtype': str(row.dtype), 'size': size}


class Recorder:
    def __init__(self, runner):
        self.runner = runner
        self.debugger = None
        self.frame = self.pending = self.folder = None
        self.size = runner.model_config.get_vocab_size()
        model = runner.get_model()
        self.modules = [name for name, _ in model.named_modules() if name]
        layers = [name for name in self.modules if re.search(r'(?:^|\.)layers\.\d+$', name)]
        count = runner.model_config.hf_text_config.num_hidden_layers
        parallel = runner.vllm_config.parallel_config
        if (not runner.model_config.enforce_eager or runner.use_async_scheduling or runner.speculative_config
                or parallel.world_size != 1 or parallel.data_parallel_size != 1 or runner.debugger is not None):
            raise ValueError('实际 runner 要求 Eager、单 rank、同步、普通 greedy、独占 debugger')
        if len(layers) != count:
            raise ValueError(f'真实 decoder 层清单 {len(layers)} 与模型配置 {count} 不符')
        import torch.distributed as dist
        rank = dist.get_rank() if dist.is_initialized() else 0
        if rank != 0:
            raise ValueError('实际 rank 非 0')
        self.install_sampler()
        self.coverage = Coverage(self, model)
        state.ready('worker', rank=rank, mode='eager', synchronous=True, vocab_size=self.size, layers=layers,
                    modules=self.modules, coverage_capability='modules_and_registered_apis',
                    hooks=['_model_forward', 'compute_logits', 'sample_tokens', 'greedy_sample'])

    def install_sampler(self):
        sampler = self.runner.sampler
        original = sampler.greedy_sample
        def greedy(logits):
            if self.frame is not None:
                self.observe(self.scores, logits)
            result = original(logits)
            if self.frame is not None:
                self.observe(self.candidate, result)
            return result
        sampler.greedy_sample = greedy

    def observe(self, function, *args):
        try:
            return function(*args)
        except Exception as error:
            state.fail(self.folder, error)
            return None

    def scores(self, logits):
        if not self.runner.input_batch.sampling_metadata.all_greedy:
            raise ValueError('实际请求未进入纯 greedy 分支')
        self.frame['scores'] = save_vector(self.folder / (self.frame['forward_id'] + '-scores.bin'), logits, self.size)

    def candidate(self, result):
        self.frame['candidate'] = int(result.item())

    def select(self, schedule):
        self.frame = self.pending = None
        self.folder, internal = state.mapped()
        if internal not in schedule.num_scheduled_tokens:
            return
        if len(schedule.num_scheduled_tokens) != 1:
            raise ValueError('目标前向混入其他请求')
        self.frame = {'internal_id': internal, 'forward_id': uuid.uuid4().hex,
                      'count': schedule.num_scheduled_tokens[internal]}

    def start(self, ids, positions):
        from msprobe.pytorch import PrecisionDebugger
        frame = self.frame
        runner = self.runner
        if list(runner.input_batch.req_ids) != [frame['internal_id']]:
            raise ValueError('worker 实际批内请求不匹配')
        count = frame['count']
        valid = ids if ids is not None else runner.input_ids.gpu
        pos = positions[..., :count].detach().cpu().tolist()
        axes = pos if positions.ndim == 2 else [pos]
        if any(axis != axes[0] for axis in axes):
            raise ValueError('文本前向位置轴不一致')
        frame.update(input_ids=valid[:count].tolist(), positions=axes[0], position_axes=axes, batch_row=0,
                     logits_indices=runner.logits_indices.tolist(),
                     output_index=len(runner.requests[frame['internal_id']].output_token_ids))
        if self.debugger is None:
            self.debugger = PrecisionDebugger(str(state.root() / 'capture-config.json'))
            self.coverage.attach(self.debugger)
        self.coverage.start()
        state.write(self.folder / ('begin-' + frame['forward_id'] + '.json'), frame)
        self.debugger.start(model=runner.get_model(), rank_id=0)
        service = self.debugger.service
        frame['step'] = service.current_iter
        frame['dump'] = str(Path(service.dump_iter_dir).relative_to(state.root()) / 'rank0')

    def close(self):
        if self.debugger is not None:
            try:
                self.debugger.stop()
            finally:
                self.debugger.step()
        if getattr(self, 'coverage', None):
            self.frame['coverage'] = self.coverage.finish()

    def logits(self):
        pending = self.runner.execute_model_state
        if pending is None or pending.logits is None:
            return
        self.pending = pending
        self.frame['raw'] = save_vector(self.folder / (self.frame['forward_id'] + '-raw.bin'), pending.logits, self.size)

    def finish(self, result):
        frame = self.frame
        ids = result.sampled_token_ids[result.req_id_to_index[frame['internal_id']]]
        state.write(self.folder / (frame['forward_id'] + '.json'), frame)
        if not ids:
            return
        if ids != [frame['candidate']]:
            raise ValueError('runner 的候选与送往引擎的采样结果不同')
        index = frame['output_index']
        dest = self.folder / f'prediction-{index}.json'
        if dest.exists():
            raise ValueError('同一请求的输出序号重复')
        state.write(dest, frame)


def install(cls):
    load, execute, forward, sample = cls.load_model, cls.execute_model, cls._model_forward, cls.sample_tokens
    def loaded(self, *args, **kwargs):
        result = load(self, *args, **kwargs)
        self._verify_recorder = None
        try:
            self._verify_recorder = Recorder(self)
        except Exception as error:
            state.fail(state.root(), error)
        return result
    def scheduled(self, scheduler_output, *args, **kwargs):
        rec = getattr(self, '_verify_recorder', None)
        if rec:
            rec.observe(rec.select, scheduler_output)
        try:
            result = execute(self, scheduler_output, *args, **kwargs)
        finally:
            if rec and rec.frame:
                rec.observe(rec.close)
                state.write(rec.folder / ('end-' + rec.frame['forward_id'] + '.json'),
                            {**rec.frame, 'status': 'failed' if sys.exc_info()[0] else 'completed'})
        if rec and rec.frame:
            rec.observe(rec.logits)
        return result
    def invoked(self, num_tokens_padded, input_ids=None, positions=None, *args, **kwargs):
        rec = getattr(self, '_verify_recorder', None)
        if not rec or not rec.frame:
            return forward(self, num_tokens_padded, input_ids, positions, *args, **kwargs)
        rec.observe(rec.start, input_ids, positions)
        return forward(self, num_tokens_padded, input_ids, positions, *args, **kwargs)
    def sampled(self, *args, **kwargs):
        rec = getattr(self, '_verify_recorder', None)
        capturing = rec and rec.frame
        if capturing and rec.pending is not self.execute_model_state:
            state.fail(rec.folder, '采样前 runner 状态已被替换')
        try:
            result = sample(self, *args, **kwargs)
            if capturing:
                rec.observe(rec.finish, result)
            return result
        finally:
            if rec:
                rec.frame = rec.pending = None
    cls.load_model, cls.execute_model, cls._model_forward, cls.sample_tokens = loaded, scheduled, invoked, sampled
