---
name: vllm-precision-compare
description: 比较 GPU/NPU 总分、分项和逐题评分，核对同前文的 Token 预测，或在首异常前向比较层与算子张量并按调用进行 CPU 复核。
---

# 精度比较

`--action tensor` 按冻结计划校验单端张量包或比较 GPU、NPU 两端的同一调用，并复算有等价 CPU 定义的输出。选定范围、按张量类型确定的数值阈值和覆盖回执见[张量与算子比较契约](references/tensor.md)。

`--action token` 读取已核验的单题预测记录，在首个具有双端预测的异常位置现场复算 CPU，记录两端误差与实际计算精度。CPU 数据缺失时保留 mismatch。最小采集字段、绝对容差及一基位置见 [Token 比较契约](references/token.md)。

## 任务质量对齐

使用 `python3 scripts/compare.py --request /abs/request.json --output /abs/comparison.json` 比较已取得的测量；也可用 `--work-root /abs/runs --run-id ID` 写入独立尝试。加 `--plan` 只读检查请求形状和输出边界。文件引用、示例和外层操作入口见 [请求示例](assets/request.example.json)。

`baseline` 沿用客户的一项 GPU 基线。提供绝对路径 `measurement` 指向 NPU 原生质量证据、`receipt` 指向原始 NPU 测量，`source` 记录 `reused` 或 `rerun`。复用普通历史评测时，另传 `verification` 指向重新核验原始测量并导出本次证据的 `quality-analyze` 回执。

默认以客户 `baseline.scores[].gpu_score` 为准，报告 `comparison_basis=customer_provided`。NPU 分数须由已完成、测量有效的 `verify-agent-plus` 回执以路径和摘要绑定，并按数据集、指标和可选子集匹配。按客户声明的 `sample_results` 和 NPU 原生逐题成绩配对，缺失或重复的题号进入 `unmatched`。默认只按同一子集的原始题号配对，省略子集时题号须在所选范围内唯一。

仅依赖公开 GPU 分数时填 `baseline_source=publisher` 并提供已核验的 `gpu_evidence`。客户已提供 GPU 分数时，额外材料可用来取得发布者的对应阈值。`target_source` 分别记录显式客户阈值（`customer`）、适用于同一指标和范围的发布阈值（`publisher`）、GPU 基线的 1%（`default_relative`）或保守零阈值（`default_zero`）。显式零有效。比例类指标沿用原生单位，自定义指标可在原生定义中指定 `scale_type`。零基线允许退化量为零，任意零点的分数默认取零。

质量报告为每个有效指标给出 GPU 与 NPU 分数、`npu_minus_gpu`、有方向的 `drop`、`target.max_drop` 和来源。下降量等于阈值时通过。任一已确认超限即报告 `fail`。证据缺项单独列出。完整报告的退出码为 0，请求、NPU 来源或输出无效时退出 2。

## 导入 GPU 原生 EvalScope 运行

用 `python3 scripts/compare.py --inspect-gpu /abs/index.json` 可在调度 NPU 之前单独读取并核验 GPU 归档。成功时标准输出给出冻结协议、原生总分与分项分数、生效静态配置及来源引用，错误退出码为 2。后续定向重跑如需从已有原生资料定位退化题，可使用 `--include-samples` 一并提取可用的逐题成绩。

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

`source` 是 GPU 侧同一轮运行的存档记录，提供 `hardware=gpu`、`status=completed`、`run_id`、`files`（原生文件路径到摘要的映射）和当时的 `protocol`。协议记录实际工具和任务版本、权重身份、数据版本和内容身份、子集及样本选择摘要、提示、生成、评分及每个指标的原生 selector。总分比较用 `sample_counts` 记录各子集有效样本数，也可由原始稳定题号列表得出；逐题比较用 `sample_ids` 按子集列出原生 review index 对应的稳定题号。只有与原始生成配置、任务选择和模型名称相符且完成全部选定样本的报告可用于正式比较。逐题核对时按 index 校验原生 review 的 sample_id、成功状态和实际评分值，再关联到稳定题号。归档可保留原始的静态模板、分词和生成配置，用于描述该次运行。声明自定义评分规则时，索引以 `scoring_proof` 和 `adapter` 绑定同轮评分映射和适配器源码。

该通路读取原生 report 的 `score`、类别下的子集分数、计数、单位和聚合，未提交的分数或逐题证据仅影响依赖它的比较项。

发布者在同次原始 GPU 运行记录 `source.scores[]` 中可以注明 `metric`、可选 `subset`、`gpu_score` 与 `target: {"max_drop": 0.005}`。阈值采用该指标原生单位。自动发现只采用原记录中与原生指标、范围及评分唯一对应的显式非负数值。比较分项的 `target_evidence.kind=publisher` 和 `source` 引用指向原始发布文件。总分与每个子集分别核对目标，缺少对应发布阈值时采用该指标的默认值。

质量比较按数据集、指标及可选子集匹配分数。GPU 与 NPU 的请求报文、逐步 Token、logits 或内部张量均非本任务输入。某项已约定目标未通过时，报告的 `next_action.failed_scores` 列出适合下一阶段复测的指标与子集，后续采集由下一阶段独立发起。
