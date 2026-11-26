"""采集实际 runner 前向，按执行模式登记 worker 能力。"""
from pathlib import Path
from time import perf_counter
from .architecture import discover
import sys
import uuid
from . import parallel as tp

from . import state
from .batch import worker as batch_worker
from .joint import window as joint
from .alignment import worker as targeted
from .decode_cache.runtime import observe_decode, prepare_worker, selected
from .coverage import Coverage
from .eager_calls import EagerCalls


def vector_snapshot(tensor, size):
    import torch
    if tensor.ndim != 2 or tensor.shape != (1, size):
        raise ValueError(f'全词表行形状不符 {tensor.shape}，预期 (1,{size})')
    row = tensor[0].detach().cpu().contiguous().clone()
    return row.view(torch.uint8).numpy().tobytes(), {'dtype': str(row.dtype), 'size': size}


def persist_vector(path, snapshot):
    data, meta = snapshot
    path.write_bytes(data)
    return {'file': path.name, **meta}


def save_vector(path, tensor, size):
    return persist_vector(path, vector_snapshot(tensor, size))


def worker_hooks(rank, asynchronous):
    if rank:
        return ['_model_forward']
    sample = 'AsyncGPUModelRunnerOutput.get_output' if asynchronous else 'greedy_sample'
    return ['_model_forward', 'compute_logits', 'sample_tokens', sample]


class Recorder:
    rank = 0
    world_size = 1

    def __init__(self, runner):
        self.runner = runner
        self.debugger = None
        self.frame = self.pending = self.folder = None
        self.pending_vectors = {}
        self.size = runner.model_config.get_vocab_size()
        model = runner.get_model()
        self.modules = [name for name, _ in model.named_modules() if name]
        self.architecture = discover(model, runner.model_config.hf_text_config)
        layers = [item['path'] for item in self.architecture['layers']]
        parallel = runner.vllm_config.parallel_config
        from .async_batch.sources import runner_mode
        async_mode = runner_mode(runner, parallel)

        identity = tp.worker_group(runner)
        self.rank = identity['rank']
        self.world_size = identity.get('world_size', 1)
        role = 'worker' if self.world_size == 1 else f'worker-rank{self.rank}'
        if self.world_size > 1:
            identity['state_shards'] = tp.shards(model, runner.model_config.hf_text_config, self.world_size)
        single_eager = self.world_size == 1 and not async_mode
        if self.rank == 0 and not async_mode:
            self.install_sampler()
        self.direct_calls = EagerCalls(model, self)
        self.coverage = Coverage(self, model)
        from .image_capture import install as install_images
        install_images(self, model)
        batch_capable = batch_worker.capable(runner, self.world_size)
        if async_mode and not batch_capable:
            raise ValueError('异步采集要求 BF16、单卡、冷前缀和至少两个请求行')
        state.ready(role, **prepare_worker(self, model), **identity, mode='eager', synchronous=not async_mode,
                    batch_identity=batch_capable, batch_joint=batch_capable, vocab_size=self.size, layers=layers,
                    modules=self.modules, architecture=self.architecture, direct_eager_modules=self.direct_calls.paths,
                    coverage_capability='modules_and_registered_apis', token_prediction=single_eager,
                    tensor_alignment=single_eager and targeted.can_arm(),
                    hooks=worker_hooks(self.rank, async_mode))

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
        if self.frame.get('profile') == 'tensor_alignment' and not self.frame.get('targeted'):
            return
        if targeted.requests_prediction(self.frame):
            import torch
            # Greedy does not sample probabilities. Normalize the real pre-argmax scores in host FP64.
            probabilities = torch.softmax(logits.detach().to(device='cpu', dtype=torch.float64), dim=-1)
            self.pending_vectors['processed_probs'] = vector_snapshot(probabilities, self.size)
            return
        self.frame['scores'] = save_vector(self.folder / (self.frame['forward_id'] + '-scores.bin'), logits, self.size)

    def candidate(self, result):
        self.frame['candidate'] = int(result.item())

    def select(self, schedule):
        self.frame = self.pending = None
        self.pending_vectors = {}
        self.folder, internal = state.mapped()
        if internal not in schedule.num_scheduled_tokens:
            return
        if len(schedule.num_scheduled_tokens) != 1:
            raise ValueError('目标前向混入其他请求')
        if self.world_size > 1:
            self.frame = tp.select_frame(self, schedule, internal)
        else:
            self.frame = {'internal_id': internal, 'forward_id': getattr(schedule, '_verify_prefix_forward_id', None) or uuid.uuid4().hex,
                          'count': schedule.num_scheduled_tokens[internal]}
        intent = state.read(tp.request_folder(self.folder) / 'intent.json')
        if intent.get('profile') in ('token_prediction', 'tensor_alignment'):
            self.frame['profile'] = intent['profile']
            self.cache_selection = None
        else:
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
        if frame.get('profile') == 'tensor_alignment':
            targeted.start(self)
        elif 'step' not in frame and frame.get('profile') != 'token_prediction':
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

    def prepare_probe(self):
        from msprobe.pytorch import PrecisionDebugger
        if self.debugger is None:
            self.direct_calls.validate()
            self.debugger = PrecisionDebugger(str(state.root() / 'capture-config.json'))
            self.coverage.attach(self.debugger)
        return self.debugger

    def start_probe(self):
        frame, runner = self.frame, self.runner
        self.prepare_probe()
        self.coverage.start()
        self.probe_started = perf_counter()
        self.debugger.start(model=runner.get_model(), rank_id=self.rank)
        service = self.debugger.service
        frame['step'] = service.current_iter
        frame['dump'] = str(Path(service.dump_iter_dir).relative_to(state.root()) / f'rank{self.rank}')

    def close(self):
        if self.frame.get('profile') == 'tensor_alignment':
            return targeted.close(self)
        if self.frame.get('profile') == 'token_prediction':
            return
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
        if self.frame.get('profile') == 'tensor_alignment' and not self.frame.get('targeted'):
            return
        if targeted.requests_prediction(self.frame):
            self.pending_vectors['raw'] = vector_snapshot(pending.logits, self.size)
        else:
            self.frame['raw'] = save_vector(self.folder / (self.frame['forward_id'] + '-raw.bin'), pending.logits, self.size)

    def commit_prediction_vectors(self, frame, ids):
        snapshots, self.pending_vectors = self.pending_vectors, {}
        if not ids:
            return
        for field, suffix in (('raw', 'raw'), ('processed_probs', 'probs')):
            path = self.folder / (frame['forward_id'] + '-' + suffix + '.bin')
            frame[field] = persist_vector(path, snapshots[field])

    def finish(self, result):
        frame = self.frame
        ids = result.sampled_token_ids[result.req_id_to_index[frame['internal_id']]]
        frame_path = self.folder / (frame['forward_id'] + '.json')
        if ids and ids != [frame['candidate']]:
            state.write(frame_path, frame)
            raise ValueError('runner 的候选与送往引擎的采样结果不同')
        if targeted.requests_prediction(frame):
            self.commit_prediction_vectors(frame, ids)
        state.write(frame_path, frame)
        if not ids:
            return
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
        self._verify_batch = getattr(scheduler_output, '_verify_batch', None)
        if rec:
            rec.observe(rec.select, scheduler_output)
        try:
            result = execute(self, scheduler_output, *args, **kwargs)
        finally:
            batch, self._verify_batch = self._verify_batch, None
            if batch and rec:
                rec.observe(joint.finish, rec, batch)
            if rec and rec.frame:
                rec.observe(rec.close)
                state.write(rec.folder / ('end-' + rec.frame['forward_id'] + '.json'),
                            {**rec.frame, 'status': 'failed' if sys.exc_info()[0] else 'completed'})
        if rec and rec.frame and rec.rank == 0:
            rec.observe(rec.logits)
        return result
    def invoked(self, num_tokens_padded, input_ids=None, positions=None, *args, **kwargs):
        rec = getattr(self, '_verify_recorder', None)
        plan = getattr(self, '_verify_batch', None)
        if plan and not plan['foreign_members']:
            with batch_worker.forward(plan, self, input_ids, positions) as rows:
                if rec and plan.get('profile') == 'batch_joint':
                    with joint.model(rec, plan, rows):
                        return forward(self, num_tokens_padded, input_ids, positions, *args, **kwargs)
                return forward(self, num_tokens_padded, input_ids, positions, *args, **kwargs)
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
