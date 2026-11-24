"""Observe the seeded full-vocabulary path before Ascend mutates its probability row."""
from functools import wraps


def install(rec, save):
    import vllm_ascend.sample.sampler as backend
    sampler = rec.runner.sampler.topk_topp_sampler
    filter_logits, sample = sampler.apply_top_k_top_p, backend.random_sample

    def record_scores(logits):
        frame = rec.frame
        frame['scores'] = save(rec.folder / (frame['forward_id'] + '-scores.bin'), logits, rec.size)

    @wraps(filter_logits)
    def filtered(*args, **kwargs):
        result = filter_logits(*args, **kwargs)
        if rec.frame is not None:
            rec.observe(record_scores, result)
        return result

    def record_distribution(probs, generators):
        frame = rec.frame
        meta = rec.runner.input_batch.sampling_metadata
        if set(generators) != {0} or not meta.all_random:
            raise ValueError('需要单请求、显式 seed 的实际随机采样')
        frame['sampler'] = {'seed': generators[0].initial_seed(), 'temperature': float(meta.temperature.item()),
                            'top_k': int(meta.top_k.item()) if meta.top_k is not None else None,
                            'top_p': float(meta.top_p.item()) if meta.top_p is not None else None}
        frame['probabilities'] = save(rec.folder / (frame['forward_id'] + '-probabilities.bin'), probs, rec.size)

    @wraps(sample)
    def sampled(probs, generators):
        if rec.frame is not None:
            rec.observe(record_distribution, probs, generators)
        result = sample(probs, generators)
        if rec.frame is not None:
            rec.observe(rec.candidate, result)
        return result

    sampler.apply_top_k_top_p = filtered
    backend.random_sample = sampled
