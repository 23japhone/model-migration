"""在已审核的 Ascend wrapper 外登记捕图，并在设备 replay 返回后计数。"""
from functools import wraps
from inspect import signature
from time import perf_counter
from uuid import uuid4
from weakref import WeakKeyDictionary

from .. import state
from .replay import active, capturing

MODULE = 'vllm_ascend.compilation.acl_graph'
DIGEST = 'f891fe70cc9f5076ead734881dc984340dac2b483a5667f08af3fc9bc9a1aa77'
recorder = None


def install(module):
    wrapper = module.ACLGraphWrapper
    call = wrapper.__call__
    graph_cls = module.torch.npu.NPUGraph
    replay = graph_cls.replay
    wrappers = WeakKeyDictionary()

    @wraps(replay)
    def played(graph, *args, **kwargs):
        rec = active.get()
        timing = rec.observe(rec.start_clock) if rec is not None else None
        start = perf_counter()
        result = replay(graph, *args, **kwargs)
        if recorder is not None:
            recorder.log.replayed(graph, record=active.get() is recorder)
            if active.get() is recorder and graph in recorder.log.graphs:
                recorder.completed_graph(graph, perf_counter() - start, timing)
        return result

    @wraps(call)
    def invoked(owner, *args, **kwargs):
        rec = recorder
        if rec is None:
            return call(owner, *args, **kwargs)
        ctx = module.get_forward_context()
        if owner.runtime_mode != ctx.cudagraph_runtime_mode:
            return call(owner, *args, **kwargs)
        desc = ctx.batch_descriptor
        entry = owner.concrete_aclgraph_entries.get(desc)
        if entry is not None and entry.aclgraph is not None:
            rec.verify_inputs(entry.aclgraph, args, kwargs)
            return call(owner, *args, **kwargs)
        binding = {'buffers': {}, 'errors': [], 'read_sources': {}}
        rec.bind_inputs(binding, args, kwargs)
        token = capturing.set(binding)
        start = perf_counter()
        try:
            result = call(owner, *args, **kwargs)
        finally:
            capturing.reset(token)
        graph = owner.concrete_aclgraph_entries[desc].aclgraph
        item = rec.log.capture(graph, wrappers.setdefault(owner, uuid4().hex), repr(desc),
                               owner.runtime_mode.name, binding=binding)
        rec.captured(item, perf_counter() - start)
        return result
    wrapper.__call__ = invoked
    graph_cls.replay = played


def install_calibration(cls):
    dummy_run = cls._dummy_run
    parameters = signature(dummy_run)

    @wraps(dummy_run)
    def calibrate(owner, *args, **kwargs):
        if recorder is None:
            return dummy_run(owner, *args, **kwargs)
        options = parameters.bind(owner, *args, **kwargs).arguments
        start = perf_counter()
        with recorder.log.calibration() as graphs:
            result = dummy_run(owner, *args, **kwargs)
        phase = 'capture' if graphs else 'profile' if options.get('is_profile') else 'warmup'
        evidence = {'phase': phase, 'num_tokens': options.get('num_tokens'), 'graph_ids': graphs,
                    'elapsed_seconds': perf_counter()-start}
        recorder.observe(state.write, state.root()/('startup-'+uuid4().hex+'.json'), evidence)
        return result
    cls._dummy_run = calibrate
