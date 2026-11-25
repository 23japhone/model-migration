"""目标 worker 的 TP 组、可见设备及跨进程前向身份。"""
from . import state

EXECUTOR = 'vllm.v1.executor.multiproc_executor'
EXECUTOR_SHA = '2cca69138a2fdfe0c08cc3b3767964f63ae24e3f5bfbb2ac6826fa70c295458f'


def executor(cls):
    original = cls._get_output_rank

    def output_rank(self):
        rank = original(self)
        state.ready('executor', output_rank=rank, world_size=self.world_size, sha256=EXECUTOR_SHA)
        return rank

    cls._get_output_rank = output_rank


def worker_group(runner):
    import torch.distributed as dist
    config = runner.vllm_config.parallel_config
    rank = dist.get_rank() if dist.is_initialized() else 0
    path = state.root() / 'parallel.json'
    if not path.exists():
        if config.world_size != 1 or rank != 0:
            raise ValueError('实际 rank 与单卡声明不符')
        return {'rank': 0}
    import torch
    from vllm.distributed.parallel_state import get_tp_group
    group = get_tp_group()
    mapping = state.read(path)['devices']
    index = torch.npu.current_device()
    if (config.world_size != 2 or config.tensor_parallel_size != 2 or config.pipeline_parallel_size != 1
            or group.world_size != 2 or list(group.ranks) != [0, 1] or group.rank_in_group != rank):
        raise ValueError('实际进程组与单机 TP=2 声明不符')
    check_parallel_dimensions(config)
    backend = str(dist.get_backend(group.device_group))
    if backend != 'hccl':
        raise ValueError('实际 TP 设备通信组不是 HCCL: ' + backend)
    device, = [row for row in mapping if row['visible_index'] == index]
    return {**device, 'rank': rank, 'tp_rank': group.rank_in_group, 'world_size': group.world_size,
            'group_ranks': list(group.ranks), 'device_backend': backend}


def check_parallel_dimensions(config):
    extra_sizes = ('data_parallel_size', 'prefill_context_parallel_size', 'decode_context_parallel_size', 'nnodes', 'nnodes_within_dp')
    if getattr(config, 'enable_expert_parallel', False) or any(getattr(config, key, 1) != 1 for key in extra_sizes):
        raise ValueError('双卡仅适配纯张量并行')


def select_frame(rec, schedule, internal):
    identity = schedule._verify_forward
    if identity['internal_id'] != internal or identity['count'] != schedule.num_scheduled_tokens[internal]:
        raise ValueError('广播前向与 worker 实际调度不匹配')
    folder = rec.folder / ('rank' + str(rec.rank))
    folder.mkdir(mode=0o700, exist_ok=True)
    rec.folder = folder
    return {**identity, 'dispatch': identity, 'rank': rec.rank}


def request_folder(folder):
    return folder.parent if folder.name in ('rank0', 'rank1') else folder


def share(rec, amount):
    size = getattr(rec, 'world_size', 1)
    quotient, remainder = divmod(amount, size)
    return quotient + int(getattr(rec, 'rank', 0) < remainder)


def shards(model, config, size):
    result = {}
    for path, module in model.named_modules():
        if hasattr(module, 'impl') and hasattr(module, 'num_kv_heads'):
            local = module.num_kv_heads
            total = getattr(config, 'num_key_value_heads', None)
            if total and local * size == total:
                result[path] = {'kv_heads': local, 'global_kv_heads': total, 'layout': 'sharded_kv_heads'}
            elif total == local == 1:
                result[path] = {'kv_heads': local, 'global_kv_heads': total, 'layout': 'replicated_kv_head'}
        elif getattr(module, 'tp_size', None) == size and hasattr(module, 'num_v_heads'):
            result[path] = {'global_value_heads': module.num_v_heads, 'local_value_heads': module.num_v_heads // size,
                            'ssm_layout': 'sharded_value_heads', 'conv_layout': 'local_packed_qkv'}
    return result
