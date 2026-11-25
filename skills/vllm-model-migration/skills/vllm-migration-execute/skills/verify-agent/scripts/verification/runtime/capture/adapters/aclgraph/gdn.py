"""在分段图之外记录单请求 GDN 的实际内核入参及块终态。"""
from functools import wraps

from ..decode_cache.hooks import Names, bind_method
from .replay import active


def install_arguments(module, layer, observer):
    method = '_forward_core'
    namespace = module._forward_core.__func__.__globals__
    torch = namespace['torch']
    ops = torch.ops._C_ascend
    conv = ops.npu_causal_conv1d_custom
    recurrent = ops.npu_recurrent_gated_delta_rule
    chunk = namespace['chunk_gated_delta_rule']

    @wraps(conv)
    def convolution(*args, **kwargs):
        if active.get() and observer.binding() is observer.direct:
            observer.frame.setdefault('gdn_calls', {})[str(layer)] = {
                'conv_rows': list(kwargs['cache_indices_opt']), 'run_mode': kwargs['run_mode'],
                'query_start': list(kwargs['query_start_loc_opt']),
                'initial_mode': list(kwargs['initial_state_mode_opt'])}
        return conv(*args, **kwargs)

    @wraps(recurrent)
    def decode(*args, **kwargs):
        if observer.binding() is not None:
            observer.record(kwargs['ssm_state_indices'].reshape(-1)[:1], layer, 'ssm.row')
        return recurrent(*args, **kwargs)

    @wraps(chunk)
    def prefill(*args, **kwargs):
        observing = observer.binding() is not None
        if observing:
            initial = kwargs['initial_state'][0].transpose(-1, -2)
            observer.record(initial, layer, 'ssm.initial')
        output = chunk(*args, **kwargs)
        if observing and kwargs['output_final_state']:
            final = output[1][0].transpose(-1, -2).to(module.kv_cache[1].dtype)
            observer.record(final, layer, 'ssm.final')
        return output

    local = Names(ops, npu_causal_conv1d_custom=convolution, npu_recurrent_gated_delta_rule=decode)
    bind_method(observer.scopes, module, method, 'torch', Names(torch, ops=Names(torch.ops, _C_ascend=local)))
    bind_method(observer.scopes, module, method, 'chunk_gated_delta_rule', prefill)
