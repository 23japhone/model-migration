# 任务质量对齐的客户输入

在客户任务的 `accuracy.gpu_baselines` 中填写 GPU 已有成绩。每一项对应一套 benchmark 协议。顶层 `model` 和 `environment` 仍用于定位 NPU 部署。`accuracy.datasets` 及其 `limit` 用于普通精度测试。基线复测独立选择数据、提示、生成和评分条件。[完整输入示例](../assets/task-quality.example.json)

## 每份 GPU 基线

| 字段 | 填写方法 |
| --- | --- |
| evaluator、dataset | 必填，填写评测工具和原生 benchmark 名称。当前独立质量复测使用 EvalScope，其他 GPU 工具的分数可与同指标的有效 NPU 成绩配对。 |
| scores | 非空数组，每项必填 `metric`、`gpu_score`。填写原生单位的有限分数，真实零分填 0。每个 metric、subset 组合只填一次。 |
| evaluator_version | GPU 评测器的可选精确版本，随基线存档。NPU 使用本端准备阶段确定的 EvalScope 版本。 |
| subset_list | 可选非空唯一子集数组。缺失、null 或整数 -1 选择该版本本端任务实际声明的全部评测子集。每个所选子集使用全量原题。 |
| task_name、task_revision | 可记录 GPU 使用的任务名称和修订，用于解释该基线。 |
| prompt_config | 可记录 GPU 的 request_format、enable_thinking、system_prompt、prompt_template、chat_template。request_format 可填 chat 或 completion。 |
| generation_config | 可记录 GPU 的 temperature、top_p、top_k、max_tokens、seed、stop。stop 接受字符串或非空字符串列表。 |
| scoring_config | 可记录 GPU 的 scorer_name、scorer_version、answer_extraction、normalization、invalid_answer_policy。 |

`gpu_baselines` 缺失、null、整数 -1 或空数组时，仅在任务包含精度评测意图的情况下按下节流程搜索模型的公开 GPU 基线。客户明确提供基线时，每份基线的 `scores` 必须有成绩。工具或数据版本的自动补齐仅描述本次 NPU 重跑条件。客户填报的 GPU 分数按声明使用。

## 客户未提供 GPU 基线

Agent 用顶层 `model.model_repository_id` 查询 Hugging Face 发布仓库。仅有 `model.model_name` 时，优先采用已与部署权重核对的来源仓库。还没有仓库依据时，按模型名检索同名候选，逐个检查模型卡和评测资料，从原始 GPU 运行记录核实发布仓库、数据集与成绩归属。多个仓库都匹配且无法唯一归属时，记为未评估。搜索回执保留实际访问的来源和检查范围。

模型卡和评测附件按不可变提交读取。搜索包括模型仓内的评测 JSON、模型卡指定的报告文件和相关评测仓库。Agent 也可在内部 `observations.quality_baseline_sources` 提供额外的 HTTPS 附件地址，限模型仓或其官方模型卡明确链接的 GitHub、Hugging Face 发布仓库。[Hugging Face 模型卡和评测结果](https://huggingface.co/docs/hub/model-cards)

公开来源须能绑定到已完成的原始 GPU 运行。工作流核实发布仓库、原生评分报告、指标单位、作用范围及对应分值，并保存搜索 URL 和内容摘要。模型卡中缺少这些依据的展示分数作为搜索线索。无可用来源时，回执列出缺项，其他任务独立继续。准备 NPU 测量时可以沿用归档的固定题库版本和原生指标映射。

发行方可在同轮原始 GPU 记录 `source.scores[]` 中写明 `metric`、可选 `subset`、`gpu_score` 和 `target.max_drop`。对应范围内唯一、有效的发布阈值会绑定原始文件摘要。

## 每条成绩与明细

- `subset` 指定当前范围内的单个子集。省略时按所选范围比较原生总分。
- `target.max_drop` 为原生单位下允许的非负下降量，显式 0 有效。优先采用客户值，其次为同指标同范围的发布者阈值。缺省时，适合相对比较的非负基线取 GPU 分数 × 0.01；零基线或任意零点的分数取 0。例如 GPU 准确率 0.8，默认限值 0.008，NPU 准确率等于 0.792 时达标。越低越好的指标按 NPU 减 GPU 衡量退化。
- `sample_results` 中每条提供 `sample_id` 和 `score`。跨子集可补充 `subset`，单子集继承所属分项。题号使用原文件的字符串或整数 ID；没有显式 ID 时使用“相对源文件名#零基数据行号”。布尔值仅作原生逐题评分。缺失、null 或整数 -1 表示没有逐题比较请求。显式空列表、重复或未配对的题号影响逐题证据，仍保留有效的分数比较。
- 程序直接按客户提供的 GPU 基线比较，使用数据集、指标及可选子集匹配有效 NPU 成绩。题号只用于逐题比较。NPU 来源可以是本次有效测量，或按其自身测量要求复核通过的历史测量。请求的某些子集在 NPU 上缺失时，只判定已有子集分项，总分注明范围缺项。

## 客户回执中的任务质量结论

`migrate.py --task` 的终端 JSON 和本次 `customer-manifest.json` 提供同一份 `task_quality_conclusion`。例如 GPU 准确率 80%、NPU 准确率 78%、允许下降 1 个百分点时，即使三个文件已发布、退出码为 0，回执也会写明“任务已完成，任务质量未达标。ceval，总分 acc 下降 2 个百分点，允许下降 1 个百分点。”

| `precision_target` | 客户如何解读 |
| --- | --- |
| `pass` | 本次约定的任务质量目标均取得有效通过证据。子集名称和指标限定达标范围。 |
| `fail` | 至少一项已确认未达标。查看超限指标的实际下降量和允许值，同轮其余数据集的缺项仍会列出。 |
| `not_evaluated` | 缺少 GPU 基线、对应有效 NPU 指标或任务意图。历史报告按原有结论展示。 |
| `not_requested` | 客户意图已确认仅要求性能交付，且本次没有声明 GPU 质量基线。 |

`summary` 首句同时给出执行状态和任务质量结论。自动化验收读取 `precision_target`，详细范围取自 `reports[]` 的数据集和 `scores[]` 的子集、指标、两端已有成绩、分差、阈值及其来源。数值沿用原生单位，中文说明将比例差换算为百分点。`report` 保留原报告路径和 SHA256，无法取得有效报告的项保留原因。中断或阻塞时已取得的有效结论仍可查看。续接历史任务时，新回执依据本次使用的绑定报告生成摘要。Token 预测及算子与张量诊断分别查看对应层的回执。

## 客户回执中的三层对齐摘要

`migrate.py --task` 的终端 JSON 与本次 `customer-manifest.json` 使用同一个 `alignment_summary`。客户汇报按 `summary` 的顺序先说明执行结果与任务质量，再列两层诊断。自动化按验收层读取相应字段。

| 层 | 结论 | 范围、差异和缺项 |
| --- | --- | --- |
| `task_quality` | 与顶层 `task_quality_conclusion` 相同，沿用 `precision_target` | `reports[].scores[]` 按数据集、子集和指标给出分差及阈值，缺少的基线或判据保留原因。 |
| `token_prediction` | `result` 与中文 `summary` | `cases[]` 按质量报告、子集、稳定题号列出选题依据、已比较的输出位置数、首个异常或停止差异。输入不可比和缺少预测记录分别计数。 |
| `operator_tensor` | `result` 表示已采集范围内的双端数值结果，`target_status` 按双端比较给出验收结论 | 逐题沿 Token 首异常位置绑定采集计划，列出容差、调用与槽位、可核验的超限、未配对范围及 CPU 参考；`reports[]` 保留原报告路径和 SHA256。 |

诊断层的 `coverage.known` 是已定界题数，`expected` 为可确定的本次应查题数。仍有失败分项无法定界时，`expected` 为 `null`。`by_result` 统计已知各题，`by_evidence_kind` 区分运行证据和模拟证据。`gaps[]` 给出缺项所在题目或范围及原因。一题有已确认的超限和缺失槽位时，同时计入差异题数和 `cases_with_gaps`。

| 诊断层的 `result` | 客户读法 |
| --- | --- |
| `mismatch` | 已确认双端差异，其余未评估题目和槽位继续列出。 |
| `aligned` | 该层本次已定界的必要范围均有双端对照，在相应判据下已对齐。 |
| `simulated` | 仅 Token 层使用此状态。第三层的采集来源只用于说明。 |
| `insufficient_evidence` | 缺材料、输入不可比或 Token 证据来源无法核验，范围内已有结果仍可查看。 |
| `not_requested` / `not_triggered` / `not_run` | 分别表示已确认本次未要求、已核实上游无适用异常、尚未执行。 |
| `pending` | 运行中的客户回执尚待收尾汇总。 |

例如，Token 层有一道题在第 2 个位置选词不同，另一道题缺 GPU 预测记录，汇报同时写出这两个题号和各自情况。旧任务只调整张量容差后，若首轮质量仍超限、同一道题的模拟张量已一致，三行分别写“任务质量未达标”“模拟 Token 预测有差异”“本次容差下张量一致，双端张量目标已达标”。新回执从绑定的报告生成，旧回执仍可按原摘要核验。

## Agent 的准备观察

客户提供上述业务字段。C-Eval 默认从仓库登记的 ModelScope 数据集发布包按 commit 取原始 CSV，GSM8K 从原作者 GitHub 仓库按 commit 取 train/test 原始 JSONL。其他数据集由 Agent 查证相应官方来源。Agent 在运行目录外收集资料，在内部 `observations.quality_sources[dataset]` 放置解析资料对象或其 `{path,sha256}` 引用。原生目录、GPU 历史材料、官方索引正文和数据准备请求的文件身份均由 Agent 维护。

| 内部材料 | 内容 |
| --- | --- |
| repository | 原始发行方的 `https://github.com/组织/仓库` 、`https://huggingface.co/datasets/组织/仓库` 或 `https://www.modelscope.cn/datasets/组织/仓库`。按评测器的 dataset/变体核实来源；仓库元数据或提交地址不足以核对本地题库内容。 |
| snapshot_binding | 已核验的官方文件清单引用。含 repository、revision、files（源文件相对路径到 SHA256）。上游路径不同可填 upstream_paths 对照，ZIP 格式可填 archive，执行时根据不可变 revision 的官方 URL 核对实际源字节。 |
| preparation | 数据准备 Skill 的公开请求。使用完整原始数据。基线按实际 eval_split 重建选择，可继承 observations.datasets 同数据集的准备配方。对于通用数据另用 subsets 中的 name、split、source_file 映射原题。 |
| evaluator_environment、catalog | 可选。前者为实际解释器 python 和版本 version，省略时复用版本匹配的现有评测环境。优先使用本端明确配置的 EvalScope 版本；缺少本端版本时解析最新稳定版。通常直接用该解释器读取 EvalScope 原生注册任务的子集、元数据和默认生成参数。若提供离线原生 catalog，其固定引用须指向同版本的 evalscope-quality-catalog-v1 记录，并核对本地 adapter 源码摘要。 |
| metrics | 指标配置含 selector（name、aggregation、dimensions）、unit、direction、sample_metric、sample_kind。单一原生准确率可根据注册指标映射；其他指标或同名变体由 Agent 用固定版本原生证据显式绑定。 |
| 评分/变体 | NPU 执行可在本项配置 prompt_config、generation_config、scoring_config 或 task_args，省略时使用本端原生设置。自定义评分提供 scoring_proof 固定引用，含 declaration、evalscope_version、benchmark、adapter_sha256、scoring_config。实际原生运行应复核评分器源码和配置。 |
| 现有 GPU 历史资料 | 在已有 context.evidence 中传原生质量回执或 evalscope-gpu-history-v1 归档索引引用。有唯一可用的同数据集材料时可取得发布者阈值，客户分数直接采用声明值。 |

对于 `unit=ratio` 或 `percent` 的指标，默认按比率尺度处理。原生 MAE、MSE、RMSE 和困惑度同样适用相对容差。`unit=value` 的其他自定义计分默认按任意零点处理。原生指标定义可用 `scale_type=ratio` 标记具有实际零点的度量，或用 `interval` 声明任意零点。例如自定义数据集注册的 MAE 原生选择器对应以下 `quality_sources[dataset].metrics` 配置。GPU MAE 为 4 时，默认允许上升到 4.04。

```json
{"MAE": {"selector": {"name": "mae", "aggregation": "mean", "dimensions": {}}, "unit": "value", "direction": "lower", "sample_metric": "mae", "sample_kind": "numeric", "scale_type": "ratio"}}
```

首次解析网络索引后记录发行选择和原文。离线使用已取得的官方资料时，`responses` 可按官方 URL 映射到含 `url,body` 的原始索引记录引用，`content_sources` 可按不可变文件 URL 映射到含 `url,payload` 的来源记录引用，payload 为原文件引用。离线资料需对应本次选择时点。后续运行核对被冻结的版本、样本和题库字节。

## 从未达标分项进入 Token 预测对齐

在原 `task.json` 的 `accuracy` 下启用此诊断。以下数值仅供演示，两项均须由任务给定。

```json
{
  "token_alignment": {
    "raw_logits": {"atol": 0.05},
    "processed_scores": {"atol": 0.001}
  }
}
```

首轮照常完成任务评分。打开 `customer-manifest.json` 的 `token_alignment[].selection` 查看选中的子集和题号。优先选择已证实退化的题。若某学科分项未达标且无法定位退化题，可按该科已核实的完整名册选题。仅有总分且没有可靠题号时，报告待定界范围。

二遍采集面向定界内的单卡同步 Eager 文本贪心请求。新建的客户单卡服务会预备空闲适配器，在正式复测时才按题开启采集。vLLM 0.20.2 配方未指定调度模式时启用同步调度，显式要求异步调度的服务无法使用该采集方式。已存在的服务需持有支持 `token_prediction` 的部署回执。通过内部 `context.evidence` 提供与原质量报告 SHA256 绑定的 GPU 预测包，同一任务用原 run-id 和 `--resume` 续接。已有两端证据时直接比较。缺 GPU 包时先交付选题清单。二遍 NPU 原生评分和预测采自同一个 HTTP 请求。

在 `token_alignment[].token_report` 查看每题从 1 计数的 `first_anomalous_token`、首个差异的种类、CPU 复核及同轮质量退化能否复现。测试数据须显式声明 `evidence_kind=synthetic`。独立调用、预测包格式和判定细节见[阶段契约](../skills/vllm-precision-align/references/contract.md)和[单题比较契约](../skills/vllm-precision-align/skills/vllm-precision-compare/references/token.md)。

### 按失败位置运行第三遍

技能依据双端第二遍仍能共同运行的首异常位置生成 L0/L1 采集清单。可在 `accuracy.tensor_alignment` 中指定容差。使用原 run-id 补交 GPU 证据，查看 `tensor_alignment[].tensor_report` 中的设备、题号、回执和槽位坐标。[第三遍采集与续接](../skills/vllm-precision-align/references/tensor.md)
