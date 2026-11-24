"""单卡图 runner 与现有请求/采样协议之间的连接。"""
from contextlib import contextmanager
from hashlib import sha256
import sys
from time import perf_counter

from .. import state, parallel
from ..architecture import discover
from ..worker import Recorder
from . import hooks
from .replay import ReplayLog, active


class GraphRecorder(Recorder):
    def __init__(self, runner):
        self.runner = runner
        self.folder = self.frame = self.pending = None
        self.options = state.read(state.root() / 'graph.json')
        self.mode = runner.compilation_config.cudagraph_mode.name
        parallel.worker_group(runner)
        if (runner.use_async_scheduling or runner.speculative_config or not runner.use_aclgraph
                or self.mode != self.options['requested_mode']):
            raise ValueError('ACLGraph runner 实际模式或同步调度与申请不符')
        self.native = runner.debugger
        self.dump_armed = False
        if self.options['native'] != (self.native is not None):
            raise ValueError('实际原生图 debugger 与配置不匹配')
        self.log = ReplayLog(self.mode)
        self.size = runner.model_config.get_vocab_size()
        model = runner.get_model()
        self.architecture = discover(model, runner.model_config.hf_text_config)
        self.install_sampler()
        self.observer = None
        capability = {}
        if self.options['observe_cache']:
            from .observations import GraphObservations
            path = state.root() / 'decode-cache.json'
            self.observer = GraphObservations(model, self.architecture, state.read(path))
            capability = {'decode_cache': {'status': 'ready', 'full_request': True,
                          'config_sha256': sha256(path.read_bytes()).hexdigest()}}
        hooks.recorder = self
        state.ready('worker', mode='aclgraph', graph=self.options, rank=0, synchronous=True, vocab_size=self.size,
                    architecture=self.architecture, layers=[r['path'] for r in self.architecture['layers']],
                    coverage_capability='graph_replay', **capability)

    def scores(self, logits):
        if not self.runner.input_batch.sampling_metadata.all_greedy:
            raise ValueError('图模式采集需要实际 greedy 采样')
        if self.native:
            super().scores(logits)

    def disarm(self):
        if self.dump_armed:
            self.native.switch.fill_(0)
            self.dump_armed = False

    def select(self, schedule):
        self.disarm()
        self.frame = self.pending = None
        self.folder, internal = state.mapped()
        if internal not in schedule.num_scheduled_tokens:
            return
        identity = schedule._verify_forward
        if (dict(schedule.num_scheduled_tokens) != {internal: identity['count']}
                or identity['internal_id'] != internal):
            raise ValueError('图前向的调度身份不符')
        self.frame = {**identity, 'started_monotonic': perf_counter(), 'rank': 0, 'errors': [], 'graph_mode': self.mode}
        if self.native:
            self.frame['native_step'] = self.native.step_id
            self.frame['native_rank'] = self.native._rank_dir_name()
            self.native.switch.fill_(1)
            self.dump_armed = True
        self.timings = []
        self.log.start()
        if self.observer:
            self.observer.start(self.folder, self.frame)

    def start(self, ids, positions):
        if list(self.runner.input_batch.req_ids) != [self.frame['internal_id']]:
            raise ValueError('图 worker 实际批内请求不符')
        self.input_metadata(ids, positions)
        state.write(self.folder / ('begin-' + self.frame['forward_id'] + '.json'), self.frame)

    def bind_inputs(self, binding, args, kwargs):
        if self.observer:
            from mindie_debugger.aclgraph import ACLGraphAddressTracker
            tracker = ACLGraphAddressTracker()
            binding['addresses'] = tracker.bind_capture_inputs(args=args, kwargs=kwargs)
            binding['tracker'] = tracker

    def verify_inputs(self, graph, args, kwargs):
        if not self.observer or active.get() is not self:
            return
        item = self.log.graphs.get(graph)
        if item:
            from mindie_debugger.aclgraph import ACLGraphAddressTracker
            binding = item['binding']
            current = ACLGraphAddressTracker().bind_capture_inputs(args=args, kwargs=kwargs)
            if current.keys() != binding['addresses'].keys():
                self.frame['errors'].append('图静态输入张量集合变化')
            report = binding['tracker'].verify_replay_inputs(args=args, kwargs=kwargs)
            self.frame.setdefault('address_checks', []).append({
                'graph_id': item['graph_id'], 'next_ordinal': item['ordinal']+1,
                'checked_inputs': list(current), 'status': report['status']})
            if report['violation_count']:
                self.frame['errors'].append(str(report['first_violation']))

    def captured(self, item, elapsed):
        record = {k: v for k, v in item.items() if k != 'binding'}
        record.update(capture_seconds=elapsed, static_inputs=item['binding'].get('addresses', {}))
        state.write(state.root() / ('graph-' + item['graph_id'] + '.json'), record)

    @staticmethod
    def start_clock():
        import torch
        start = torch.npu.Event(enable_timing=True)
        end = torch.npu.Event(enable_timing=True)
        start.record()
        return start, end

    def collect_times(self):
        for event, start, end in self.timings:
            end.synchronize()
            event['device_seconds'] = start.elapsed_time(end) / 1000

    def completed_graph(self, graph, elapsed, timing):
        item = self.log.graphs[graph]
        event = self.log.events[-1]
        event['enqueue_seconds'] = elapsed
        if timing is not None:
            start, end = timing
            self.observe(end.record)
            self.timings.append((event, start, end))
        if self.observer:
            self.observer.replayed(item)

    @contextmanager
    def observing(self, padded):
        from vllm.forward_context import get_forward_context
        self.frame['graph_mode'] = get_forward_context().cudagraph_runtime_mode.name
        token = active.set(self)
        self.frame['padded_tokens'] = padded
        self.frame['mamba_mode'] = getattr(self.runner.cache_config, 'mamba_cache_mode', 'none')
        start = perf_counter()
        try:
            yield
        finally:
            active.reset(token)
            self.frame['forward_seconds'] = perf_counter() - start

    def close(self):
        if sys.exc_info()[0] is not None:
            self.disarm()
            return
        import torch
        self.frame['peak_npu_allocated_bytes'] = torch.npu.max_memory_allocated(self.runner.device)
        self.observe(self.collect_times)
        result = self.log.finish()
        self.frame['replays'] = result['replays']
        self.frame['replay_errors'] = result['errors']
        self.frame['errors'].extend(result['errors'])
        if self.observer:
            self.observer.finish()

    def logits(self):
        if self.native:
            super().logits()
        else:
            self.pending = self.runner.execute_model_state

    def finish(self, result):
        if self.native and self.native.step_id != self.frame['native_step'] + 1:
            self.frame['errors'].append('msProbe 归档步号没有对应这一次前向')
        self.frame['finished_monotonic'] = perf_counter()
        super().finish(result)
