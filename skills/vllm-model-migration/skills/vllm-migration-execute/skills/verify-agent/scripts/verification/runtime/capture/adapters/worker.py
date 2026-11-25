"""在同一同步 runner 的 execute/sample 状态间传递前向身份。"""
from pathlib import Path
from time import perf_counter
from .architecture import discover
import sys
import uuid
from . import parallel as tp

from . import state
from .decode_cache.runtime import observe_decode, prepare_worker, selected
from .coverage import Coverage
from .eager_calls import EagerCalls


def save_vector(path, tensor, size):
    import torch
    if tensor.ndim != 2 or tensor.shape != (1, size):
        raise ValueError(f'全词表行形状不符 {tensor.shape}，预期 (1,{size})')
    row = tensor[0].detach().cpu().contiguous().clone()
    path.write_bytes(row.view(torch.uint8).numpy().tobytes())
    return {'file': path.name, 'dtype': str(row.dtype), 'size': size}


class Recorder:
    rank = 0
    world_size = 1

    def __init__(self, runner):
        self.runner = runner
        self.debugger = None
        self.frame = self.pending = self.folder = None
        self.size = runner.model_config.get_vocab_size()
        model = runner.get_model()
        self.modules = [name for name, _ in model.named_modules() if name]
        self.architecture = discover(model, runner.model_config.hf_text_config)
        layers = [item['path'] for item in self.architecture['layers']]
        parallel = runner.vllm_config.parallel_config
        if (not runner.model_config.enforce_eager or runner.use_async_scheduling or runner.speculative_config
                or parallel.data_parallel_size != 1 or runner.debugger is not None):
            raise ValueError('实际 runner 要求同步 Eager、DP=1、单请求采样和独占 debugger')

        identity = tp.worker_group(runner)
        self.rank = identity['rank']
        self.world_size = identity.get('world_size', 1)
        role = 'worker' if self.world_size == 1 else f'worker-rank{self.rank}'
        if self.world_size > 1:
            identity['state_shards'] = tp.shards(model, runner.model_config.hf_text_config, self.world_size)
        if self.rank == 0:
            self.install_sampler()
        self.direct_calls = EagerCalls(model, self)
        self.coverage = Coverage(self, model)
        from .image_capture import install as install_images
        install_images(self, model)
        state.ready(role, **prepare_worker(self, model), **identity, mode='eager', synchronous=True, vocab_size=self.size, layers=layers,
                    modules=self.modules, architecture=self.architecture, direct_eager_modules=self.direct_calls.paths,
                    coverage_capability='modules_and_registered_apis',
                    hooks=['_model_forward', 'compute_logits', 'sample_tokens', 'greedy_sample'] if self.rank == 0 else ['_model_forward'])

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
        if hasattr(sampler, 'topk_topp_sampler') and self.world_size > 1:
            from .random_sampling import install
            install(self, save_vector)

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
        if self.world_size > 1:
            self.frame = tp.select_frame(self, schedule, internal)
        else:
            self.frame = {'internal_id': internal, 'forward_id': uuid.uuid4().hex,
                          'count': schedule.num_scheduled_tokens[internal]}
        selected(self, schedule)

    def start(self, ids, positions):
        frame = self.frame
        runner = self.runner
        if list(runner.input_batch.req_ids) != [frame['internal_id']]:
            raise ValueError('worker 实际批内请求不匹配')
        if 'step' in frame:
            from .decode_cache.snapshots import observer_only
            with observer_only():
                self.input_metadata(ids, positions)
        else:
            self.input_metadata(ids, positions)
        state.write(self.folder / ('begin-' + frame['forward_id'] + '.json'), frame)
        if 'step' not in frame:
            self.start_probe()

    def input_metadata(self, ids, positions):
        frame, runner = self.frame, self.runner
        count = frame['count']
        valid = ids if ids is not None else runner.input_ids.gpu
        pos = positions[..., :count].detach().cpu().tolist()
        axes = pos if positions.ndim == 2 else [pos]
        absolute = runner._positions_np_buf[:count].tolist() if 'vision' in frame else axes[0]
        if 'vision' not in frame and any(axis != axes[0] for axis in axes):
            raise ValueError('文本前向位置轴不一致')
        frame.update(input_ids=valid[:count].tolist(), positions=absolute, position_axes=axes, batch_row=0,
                     logits_indices=runner.logits_indices.tolist(),
                     output_index=len(runner.requests[frame['internal_id']].output_token_ids))

    def start_probe(self):
        from msprobe.pytorch import PrecisionDebugger
        frame, runner = self.frame, self.runner
        if self.debugger is None:
            self.direct_calls.validate()
            self.debugger = PrecisionDebugger(str(state.root() / 'capture-config.json'))
            self.coverage.attach(self.debugger)
        self.coverage.start()
        self.probe_started = perf_counter()
        self.debugger.start(model=runner.get_model(), rank_id=self.rank)
        service = self.debugger.service
        frame['step'] = service.current_iter
        frame['dump'] = str(Path(service.dump_iter_dir).relative_to(state.root()) / f'rank{self.rank}')

    def close(self):
        if self.debugger is not None:
            try:
                self.debugger.stop()
            finally:
                self.debugger.step()
                self.frame['probe_window_seconds'] = perf_counter() - self.probe_started
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
            if (state.root() / 'graph.json').is_file():
                from .aclgraph.worker import GraphRecorder
                self._verify_recorder = GraphRecorder(self)
            else:
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
        if rec and rec.frame and rec.rank == 0:
            rec.observe(rec.logits)
        return result
    def invoked(self, num_tokens_padded, input_ids=None, positions=None, *args, **kwargs):
        rec = getattr(self, '_verify_recorder', None)
        if not rec or not rec.frame:
            return forward(self, num_tokens_padded, input_ids, positions, *args, **kwargs)
        rec.observe(rec.start, input_ids, positions)
        window = rec.observing(num_tokens_padded) if hasattr(rec, 'observing') else observe_decode(rec)
        with window:
            return forward(self, num_tokens_padded, input_ids, positions, *args, **kwargs)
    def sampled(self, *args, **kwargs):
        rec = getattr(self, '_verify_recorder', None)
        capturing = rec and rec.frame and rec.rank == 0
        if capturing and rec.pending is not self.execute_model_state:
            state.fail(rec.folder, '采样前 runner 状态已被替换')
        try:
            result = sample(self, *args, **kwargs)
            if capturing:
                rec.observe(rec.finish, result)
            return result
        finally:
            if rec:
                if hasattr(rec, 'disarm'):
                    rec.observe(rec.disarm)
                rec.frame = rec.pending = None
    cls.load_model, cls.execute_model, cls._model_forward, cls.sample_tokens = loaded, scheduled, invoked, sampled
