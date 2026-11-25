---
name: vllm-precision-compare
description: 按 GPU 基线实际评测条件比较 NPU 总分、分项及可选逐题评分，记录可比性、下降量目标和证据覆盖。
---

# 任务质量对齐

使用 `python3 scripts/compare.py --request /abs/request.json --output /abs/comparison.json` 比较已取得的测量；也可用 `--work-root /abs/runs --run-id ID` 写入独立尝试。加 `--plan` 只读检查请求形状和输出边界。文件引用、示例和外层操作入口见 [请求示例](assets/request.example.json)。

`baseline` 沿用客户的一项 GPU 基线。提供绝对路径 `measurement` 指向 NPU 原生质量证据、`receipt` 指向原始 NPU 测量，`source` 记录 `reused` 或 `rerun`。复用普通历史评测时，另传 `verification` 指向重新核验原始测量并导出本次证据的 `quality-analyze` 回执。

可选 `gpu_evidence` 指向原始 GPU 测量回执。NPU 原始回执须是已完成、测量有效的 `verify-agent-plus` 测量；`detailed_report.hardware` 表示实际设备，原始或复核回执的 `evidence.quality_evidence` 以 `{path,sha256}` 绑定原生质量证据。GPU 历史 `quality-run` 回执须能从 `detailed_report.files` 重读原生报告、配置与逐题记录，也可通过 `evidence.gpu_history` 引用下述归档索引。若提供带 `origin:{path,sha256}` 的投影，程序核对原始 GPU 回执与协议，并直接取原生报告中的成绩。

证据文件采用 `format=task-quality-native-v1`，包含 `hardware`、原生 `protocol`、按作用范围列出的 `scores`。每项以 `metric` 与 `subset` 唯一匹配；`subset=null` 表示数据集总分。指标身份、聚合、方向、单位、题目和模型等条件须一致。只有声明分数缺少历史来源时报告未评估并保留有效 NPU 测量。仅 `sample_results` 触发逐题评分核对。按数据集汇总匹配时有跨子集重号的题目须注明子集；缺失、重复或冲突的记录进入 `unmatched`。

任务报告的 `conclusions` 分别给出单端测量有效性、可比性和目标达标状态。`scores` 列出各项分数、下降方向及理由。未设目标时仍交付可比项的 `npu_minus_gpu` 和 `drop`；下降量等于 `max_drop` 时达标。写出完整报告后的退出码为 0，报告内分别记录各项 `fail` 或 `not_evaluated`。请求、NPU 来源或输出无效时退出 2 并报告原因。

## 导入 GPU 原生 EvalScope 运行

用 `python3 scripts/compare.py --inspect-gpu /abs/index.json` 可在调度 NPU 之前单独读取并核验 GPU 归档。成功时标准输出给出冻结协议、原生总分与分项分数、生效静态配置及来源引用，错误退出码为 2。

已存档的原生 EvalScope v2 评测可使用 `evalscope-gpu-history-v1` 索引。该索引由调用方用已有材料整理，`source`、`config`、`report` 和可选 `reviews` 下的每个值都是原始文件的 `{path,sha256}` 引用。例如：

```json
{
  "format": "evalscope-gpu-history-v1",
  "source": {"path": "/artifacts/gpu/original-run.json", "sha256": "<原始运行记录摘要>"},
  "config": {"path": "/artifacts/gpu/task-config.json", "sha256": "<同轮原生 JSON 配置摘要>"},
  "report": {"path": "/artifacts/gpu/report.json", "sha256": "<同轮原生 v2 report 摘要>"},
  "reviews": {
    "accountant": {"path": "/artifacts/gpu/ceval_accountant.jsonl", "sha256": "<同轮原生 reviews 摘要>"}
  }
}
```

`source` 是 GPU 侧同一轮运行的存档记录，提供 `hardware=gpu`、`status=completed`、`run_id`、`files`（原生文件路径到摘要的映射）和当时的 `protocol`。协议记录实际工具和任务版本、权重身份、数据版本和内容身份、子集及样本选择摘要、提示、生成、评分及每个指标的原生 selector。总分比较用 `sample_counts` 记录各子集有效样本数，也可由原始稳定题号列表得出；逐题比较用 `sample_ids` 按子集列出原生 review index 对应的稳定题号。只有与原始生成配置、任务选择和模型名称相符且完成全部选定样本的报告可用于正式比较。逐题核对时按 index 校验原生 review 的 sample_id、成功状态和实际评分值，再关联到稳定题号。归档的 `source.static_conditions`（或索引中同名的固定文件引用）提供实际选用的模板内容摘要 `chat_template_sha256`、分词资源摘要 `tokenizer_sha256` 及 `server_generation`。后者含静态服务生成参数 `overrides`（包括选用的 `--generation-config` 来源）及模型原生生成配置文件摘要 `model_generation_files`。原生生成配置省略 `max_tokens` 时，正式相对判定还需双方静态配置给出相同的服务生效输出上限 `effective_max_tokens`；缺少该证据时仍保留 NPU 测量。旧称 `runtime_conditions` 的材料仅读取这些静态身份。声明自定义评分规则时，索引另以 `scoring_proof` 和 `adapter` 绑定同轮评分映射和实际适配器源码。

评测前已保存的 `reader_sha256_by_subset` 按子集记录选定的实际题目、示例和顺序。两端均提供时逐子集核对，随机 few-shot 缺少共同的明确选例种子时用该摘要验证相同示例。

该通路读取原生 report 的 `score`、类别下的子集分数、计数、单位和聚合，未提交的分数或逐题证据仅影响依赖它的比较项。

发布者在同次原始 GPU 运行记录 `source.scores[]` 中可以注明 `metric`、可选 `subset`、`gpu_score` 与 `target: {"max_drop": 0.005}`。阈值采用该指标原生单位。自动发现只采用原记录中与原生指标、范围及评分唯一对应的显式非负数值。比较分项的 `target_evidence.kind=publisher` 和 `source` 引用指向原始发布文件。总分与每个子集分别核对目标，缺少对应依据时该项保留分差。

质量比较仅依据评测条件和数据集评分。GPU 与 NPU 的请求报文、逐步 Token、logits 或内部张量均非本任务输入。某项已约定目标未通过时，报告的 `next_action.failed_scores` 列出适合下一阶段复测的指标与子集，后续采集由下一阶段独立发起。
