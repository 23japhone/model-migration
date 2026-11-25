"""在冻结的原生 reader 输出上选题，然后复用 EvalScope evaluator 完成预测和评分。"""
from pathlib import Path
import os
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from precision.evidence import load
from precision.runtime.evalscope.samples import sample_identity


def choose(datasets, subset, index, expected):
    filtered = datasets[subset].filter(lambda item: type(item.id) is int and item.id == index)
    if len(filtered) != 1 or sample_identity(filtered[0]) != expected:
        raise ValueError('定向题目与冻结的原生提示、few-shot 或答案不匹配')
    return {subset: filtered}


def run(payload):
    from evalscope.api.model.lazy_model import LazyModel
    from evalscope.api.registry import create_evaluator, get_benchmark
    from evalscope.run import setup_work_directory
    from evalscope.utils.model_utils import seed_everything
    from precision.runtime.evalscope.configuration import native_config
    config = native_config(payload['config'])
    if config.seed is not None:
        seed_everything(config.seed)
    outputs = setup_work_directory(config)
    adapter = get_benchmark(payload['benchmark'], config)
    adapter.validate_judge_strategy()
    selected = choose(adapter.load_dataset(), payload['subset'], payload['index'], payload['expected'])
    # 在本次 evaluator 的实例中复用刚刚核对的原生 Sample，评分和模型仍由 EvalScope 驱动。
    adapter.load_dataset = lambda: selected
    evaluator = create_evaluator(benchmark=adapter, model=LazyModel(task_config=config),
                                 outputs=outputs, task_config=config)
    evaluator.eval()


if __name__ == '__main__':
    os.environ.update(HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    run(load(sys.argv[1]))
