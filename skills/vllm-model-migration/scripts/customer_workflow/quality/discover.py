"""在目标 EvalScope 解释器中读取该版本注册的原生参数，不推理、不加载模型。"""
import hashlib
from pathlib import Path
import sys


def inspect(name):
    import dataclasses
    import importlib.metadata
    import json
    from evalscope import TaskConfig
    from evalscope.api.registry import get_benchmark

    cfg = TaskConfig(model='quality-metadata', datasets=[name], eval_type='openai_api',
                     eval_backend='Native', api_url='http://127.0.0.1/v1', api_key='EMPTY')
    adapter = get_benchmark(name, cfg)
    source = Path(sys.modules[type(adapter).__module__].__file__).resolve()
    metadata = json.loads(json.dumps(dataclasses.asdict(adapter.benchmark_meta), default=str))
    settings = cfg.model_dump(mode='json').get('generation_config') or {}
    supported = ('max_tokens', 'temperature', 'seed', 'top_p', 'top_k', 'stop_seqs',
                 'frequency_penalty', 'presence_penalty', 'repetition_penalty')
    return {'format': 'evalscope-quality-catalog-v1', 'name': name, 'evaluator_version': importlib.metadata.version('evalscope'),
            'metadata': metadata, 'eval_split': adapter.eval_split, 'subset_names': list(adapter.subset_list),
            'generation_defaults': {k: settings[k] for k in supported if settings.get(k) is not None},
            'adapter_source': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}


if __name__ == '__main__':
    from contextlib import redirect_stdout
    import json
    import os
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    with redirect_stdout(sys.stderr):
        result = inspect(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
