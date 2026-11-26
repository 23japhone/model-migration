# 任务质量测量与只读复核

精度比较阶段调用本任务的 `scripts/validator.py`，沿用 `--request --work-root --run-id` 和可选的 `--plan`。数据准备任务提供已核对的选题清单，verify-agent 提供可用模型服务交接。

| 操作 | 输入与交付 |
| --- | --- |
| `--action quality-run` | 请求含 `schema_version="1.0"`、`measurement`、`protocol` 和 `scores`。一次 EvalScope 原生运行覆盖指定 benchmark 的所有选定子集，每题发送一次非流式请求。 |
| `--action analyze` | `schema_version="1.0"`、`source={path,sha256}`、`expected` 为完整的质量测量请求。核验候选回执、原生报告与逐题预测和评审、样本及原生评分口径。成功时给出原回执引用、新复核回执和本次正式推理请求数 0。 |
| `--action quality-analyze` | 同一只读入口。分析已完成的 `quality-run` 可省略 `expected`，按原样本和指标重读。 |

`measurement` 使用普通 EvalScope 测量的 deployment、dataset、evaluator、tasks 和 schema_version。评测器须指定实际版本并使用本地文本、规则评分和单次生成。每个子集可通过 `datasets[subset]` 绑定各自的数据准备回执，同一 benchmark 的各份回执须覆盖同一数据根和文件清单。

`protocol` 包含 evaluator、evaluator_version、dataset、dataset_revision、task_name、task_revision、subsets、split、dataset_identity、model_identity、sample_identities、prompt_config、generation_config、scoring_config、metrics。上游固定官方资料和修订。测量阶段核对实际权重和模型配置文件身份、数据集和选择摘要、评测器版本与原生配置，保留生效的 TaskConfig 和 adapter 源码摘要。显式自定义评分声明随 `scoring_proof={path,sha256}` 提供，原文须绑定 declaration、evalscope_version、benchmark、adapter_sha256、scoring_config。

`scores[]` 逐项声明 metric、subset、selector、sample_metric、sample_kind、unit、direction，以及可选的 scale_type（ratio 或 interval）。subset 置为空值表示所选范围的原生 benchmark 总分。selector 由原生指标名称、聚合和维度唯一选中原报告度量。sample_metric 选择原生 review 的具体分值，sample_kind 取 binary 或 numeric。各子集采用同一份原生报告中的子集分数。旧普通测量若分子集独立执行，只能提供对应子集已经产生的原生分数。

每次在包外独立尝试写 `precision-manifest.json`。成功的 `evidence.quality_evidence` 以路径和 SHA256 绑定 `quality-evidence.json`，其中 `hardware=npu`，`protocol` 和 `scores` 记录相应协议、原生数值、单位、方向、聚合与逐题证据。样本先用原行的字符串或整数 id，没有显式 id 时以原始相对文件名加 # 加零基行号定位，跨子集另保留 subset。

正式运行读本地 tokenizer 和冻结的原生消息，按实际 chat template 参数检查每题输入和输出预算，再启动一次原生评测。回执保存原生执行统计的样本数、各子集的输入长度预算，以及模板、分词文件和服务生成默认值的静态证据。该阶段不启动 HTTP 记录器，也不采集模型运行时数据。有效性、原始评分、可比性、客户目标分别由各自的回执记录。测量有效性只核对本次 NPU 评测本身。比较任务将有效分数与客户提供或公开核验得到的 GPU 分数配对。兼容读取旧请求的 `expected_static_conditions`。

同一报告内各分数独立取值。某一总分或子集值不可核实时，在该项保存空值和原因。逐题指标未提供时，记录缺失题号并保留其余题及已有原生汇总成绩。
