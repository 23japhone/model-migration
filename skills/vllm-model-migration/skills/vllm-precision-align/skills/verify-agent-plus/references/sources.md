# 评测环境与资料来源

## 运行位置

公开控制脚本使用 Python3.10+ 标准库，不导入维护后端。EvalScope 和 AISBench 分别在请求指定的解释器内运行；EvalScope 使用 Native/openai_api。解释器须符合实际发行包的 Requires-Python，并通过原生配置探测；控制脚本的解释器不构成评测环境兼容证明。

客户 migrate.py --task 每次进入准备时调用原版 install_evalscope.py，默认共用 /data/evalscope-venv；内部 context 可传安装参数。精度请求使用返回 Python 和实际版本，不额外查 latest。完整行为和依赖见[安装契约](../../../../../references/evalscope-installation.md)。perf 帮助通过不证明精度兼容；原生 API、配置、数据与结果仍须核验。

直接使用精度 `--request` 时，只有省略 evaluator.version 或明确使用 latest，`run` 才在加载配置前读取 [EvalScope 官方发布索引](https://pypi.org/pypi/evalscope/json) 或 [AISBench 官方发布索引](https://pypi.org/pypi/ais-bench-benchmark/json)，核验安装版本为当前最高未撤回稳定版；预发布、开发版不作为默认 latest。查询失败或该策略下环境版本落后时阻塞，不悄悄退选旧版本。明确 version 始终按该期望值核验，不查询 latest；客户流程传入的实际版本也走此路径。`--plan` 和 `analyze` 均不进行该联网查询。

精度 `run` 和 `--plan` 自身不安装依赖，API 评测不改变模型服务环境。客户 EvalScope 的上游准备按安装契约每次正式调用安装脚本，创建或复用共享 venv，续接固定实际版本；已有绑定先只读核验，不先安装恢复漂移环境。直接精度请求及 AISBench 使用明确的已有兼容环境；确需补依赖时，由获准准备流程按对应契约在包外处理，不修改借用或正在运行的环境。保存实际版本、依赖、源码摘要和适用的版本查询回执，历史评测可复核不依赖未来 latest 指向。

AISBench 配置是受信任的 Python 可执行资料；EvalScope 配置为 JSON/YAML，使用已安装基准和原生评分实现。运行操作只读取用户明确提供的配置，不从数据集名称推测模板或评分器，不把任意远端配置自动下载后执行。实际配置、原数据、环境及运行证据均在资源包外维护。

## 固定资料

以下资料在2026-10-16核对，源码基点为 `1e7cb37e3d5d2101db47fe3fe2272c965375a69c`；它是首版适配基点，不是所有目标环境的推荐版本。

- [AISBench 版本说明](https://github.com/AISBench/benchmark/blob/1e7cb37e3d5d2101db47fe3fe2272c965375a69c/README.md)：解释器、依赖和评测入口。
- [自定义配置](https://github.com/AISBench/benchmark/blob/1e7cb37e3d5d2101db47fe3fe2272c965375a69c/docs/source_zh_cn/advanced_tutorials/run_custom_config.md)：组合模型、数据集及评分任务。
- [原生评分实现](https://github.com/AISBench/benchmark/blob/1e7cb37e3d5d2101db47fe3fe2272c965375a69c/ais_bench/benchmark/tasks/openicl_eval.py)：JSONL 预测读取、缺失预测处理及逐样本详情。
- [vLLM0.18.0 服务契约](https://docs.vllm.ai/en/v0.18.0/serving/openai_compatible_server/)：模型名、生成参数和模型侧默认设置。
- [vLLM 可复现性](https://docs.vllm.ai/en/stable/usage/reproducibility/)：跨硬件、调度和版本的结果限制；在线资料使用前复核版本，不能据此要求 GPU/NPU 回答逐字相同。

实际适配另外核对了固定版本的 [API 尝试循环](https://github.com/AISBench/benchmark/blob/1e7cb37e3d5d2101db47fe3fe2272c965375a69c/ais_bench/benchmark/models/api_models/base_api.py) 和 [推理任务](https://github.com/AISBench/benchmark/blob/1e7cb37e3d5d2101db47fe3fe2272c965375a69c/ais_bench/benchmark/tasks/openicl_api_infer.py)：`retry` 是总尝试次数；本适配要求显式 `retry=1` 并使用 `--num-warmups 0`，避免把额外请求混入样本覆盖。

## EvalScope 已核对资料

2026-10-19 核对 EvalScope 1.13.0，tag 对应提交 `498027686e6e3cf4eb32405a3fd21c65ff9237f1`。下列固定链接用于说明已核对源码，属于研究与实测证据，不是运行版本限制。后续版本继续通过配置/API/原生产物核验，格式不兼容时报告原因，不能宣称所有未来发布已通过验收：

- [版本说明](https://github.com/modelscope/evalscope/releases/tag/v1.13.0)：基准 evaluation_version 和缓存评分兼容性。
- [原生调用](https://github.com/modelscope/evalscope/blob/v1.13.0/docs/en/get_started/basic_usage.md)：TaskConfig、run_task 和 openai_api。
- [配置实现](https://github.com/modelscope/evalscope/blob/v1.13.0/evalscope/config.py)：实际默认值、配置序列化与执行模式。
- [原生缓存](https://github.com/modelscope/evalscope/blob/v1.13.0/evalscope/api/evaluator/cache.py)：ModelResult、ReviewResult 及原始 JSONL。
- [原生报告](https://github.com/modelscope/evalscope/blob/v1.13.0/evalscope/report/report.py)：schema_version=2、执行覆盖及指标身份。
- [指标语义](https://github.com/modelscope/evalscope/blob/v1.13.0/evalscope/api/metric/semantics.py)：原始单位、方向、聚合及显示倍率。
- [尝试次数](https://github.com/modelscope/evalscope/blob/v1.13.0/evalscope/utils/function_utils.py)：retries 为总尝试次数；另外通过 model_args.max_retries=0 禁用 OpenAI SDK 重试。

依赖安装须显式在包外环境完成，运行入口不自动安装。只安装本任务所需的原生评测依赖，不默认引入其他评测后端或 all extras。两种工具的 prompt、评分和单位可不同，不能以同名数据集推断分数可直接比较。
