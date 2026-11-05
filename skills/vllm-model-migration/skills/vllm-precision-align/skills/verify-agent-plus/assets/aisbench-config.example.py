"""配置写法示例；复制到包外并替换为实际数据配置、端点和生成条件。"""
from mmengine.config import Config
from ais_bench.benchmark.models import VLLMCustomAPIChat

# 数据配置负责 reader、模板、预处理和评分器，不在 Skill 内绑定数据集名称。
datasets = Config.fromfile('/包外/实际数据配置.py').datasets
# 必须与数据准备清单中声明的 split、原始文件及样本切片一致。
datasets[0]['path'] = '/包外/实际数据根'
datasets[0]['reader_cfg']['test_range'] = '[0:2]'
models = [dict(
    attr='service', type=VLLMCustomAPIChat, abbr='explicit-model',
    model='替换为实际served-model-name', path='/包外/实际模型根',
    host_ip='127.0.0.1', host_port=12345, url='',
    stream=False, retry=1, request_rate=0, batch_size=1,
    max_out_len=4096, generation_kwargs=dict(temperature=0, top_p=1, seed=0),
)]
# 本文件只演示字段，不代表推荐阈值或正式评测生成参数。
