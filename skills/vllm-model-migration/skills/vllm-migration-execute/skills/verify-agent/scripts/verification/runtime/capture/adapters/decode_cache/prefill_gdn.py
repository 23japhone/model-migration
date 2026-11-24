"""在分块递归计算和固定源码的最终写回边界采集两种 GDN 状态。"""
from functools import wraps
from .events import GDN, one
from .hooks import around, bind_method, Names
from .snapshots import observer_only

PREFILL_OPS = ['npu_causal_conv1d_custom', 'chunk_gated_delta_rule', 'ssm_writeback']


class PrefillGDN(GDN):
    def __init__(self, store, group, schedule, mode, meta, caches):
        super().__init__(store, group, schedule['position'] + schedule['count'] - 1, mode, meta, caches)
        self.count = schedule['count']
        self.initial = bool(one(meta.has_initial_state))
        self.event.update(observation='prefill_gdn', granularity='forward_chunk',
                          has_initial_state=self.initial, initial_source='cache' if self.initial else 'zero')
        self.final = None

    def before_conv(self, args):
        row = one(args['cache_indices_opt'])
        offsets = list(args['query_start_loc_opt'])
        if (args['conv_state'] is not self.caches[0] or args['run_mode'] != 0 or offsets != [0, self.count]
                or bool(one(args['initial_state_mode_opt'])) != self.initial):
            raise ValueError('GDN prefill 卷积状态或初始模式与元数据不符')
        self.watch_row('conv', row)
        self.event['conv'].update(run_mode=0, query_start_loc=offsets)

    def watch_row(self, name, row):
        cache = self.caches[0 if name == 'conv' else 1]
        if not 0 <= row < cache.shape[0]:
            raise ValueError('GDN 状态行越界')
        view = cache[row]
        self.event[name] = {'row': row, 'plan_matches': row == self.event['expected_row'],
                            'metadata_matches': row == self.event['metadata_row']}
        self.handles[name] = view, self.snapshots.before(name, view, True)

    def before_chunk(self, args):
        import torch
        if args['cu_seqlens'].tolist() != [0, self.count] or not args['output_final_state']:
            raise ValueError('GDN 递归块长度或终态开关不符')
        self.watch_row('ssm', self.event['metadata_row'])
        initial, = args['initial_state'].unbind()
        physical = self.handles['ssm'][0]
        actual = initial.transpose(-1, -2).contiguous()
        self.snapshots.boundary('ssm.initial', actual)
        expected = physical if self.initial else torch.zeros_like(physical)
        if not torch.equal(actual, expected):
            raise ValueError('GDN 实际递归初态与清零或续块语义不符')

    def writeback(self):
        import torch
        self.after('ssm')
        physical = self.handles['ssm'][0]
        final, = self.final.unbind()
        if not torch.equal(physical, final.transpose(-1, -2).to(physical.dtype)):
            raise ValueError('GDN 递归终态与实际写回值不同')


def bind_prefill_gdn(stack, module, capture, seen, errors):
    method = '_forward_core'
    namespace = getattr(module, method).__func__.__globals__
    torch = namespace['torch']
    conv = 'npu_causal_conv1d_custom'
    op = around(getattr(torch.ops._C_ascend, conv), conv, seen, errors,
                capture.before_conv, lambda: capture.after('conv'))
    ops = Names(torch.ops, _C_ascend=Names(torch.ops._C_ascend, **{conv: op}))
    bind_method(stack, module, method, 'torch', Names(torch, ops=ops))
    chunk = 'chunk_gated_delta_rule'
    call = around(namespace[chunk], chunk, seen, errors, capture.before_chunk)

    @wraps(call)
    def observed_chunk(*args, **kwargs):
        result = call(*args, **kwargs)
        capture.final = result[1]
        return result

    bind_method(stack, module, method, chunk, observed_chunk)
    core = getattr(module, method)

    @wraps(core)
    def after_core(*args, **kwargs):
        result = core(*args, **kwargs)
        seen.append('ssm_writeback')
        try:
            with observer_only():
                capture.writeback()
        except Exception as error:
            errors.append(f'ssm_writeback: {error}')
        return result

    setattr(module, method, after_core)
