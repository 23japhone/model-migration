# 任务质量对齐的客户输入

在客户任务的 `accuracy.gpu_baselines` 中填写 GPU 已有成绩。每一项对应一套 benchmark 协议。顶层 `model` 和 `environment` 仍用于定位 NPU 部署。`accuracy.datasets` 及其 `limit` 用于普通精度测试。基线复测独立选择数据、提示、生成和评分条件。[完整输入示例](../assets/task-quality.example.json)

## 每份 GPU 基线

| 字段 | 填写方法 |
| --- | --- |
| evaluator、dataset | 必填，填写评测工具和原生 benchmark 名称。当前独立质量复测使用 EvalScope，其他工具保留声明并报告执行能力限制。 |
| scores | 非空数组，每项必填 `metric`、`gpu_score`。填写原生单位的有限分数，真实零分填 0。每个 metric、subset 组合只填一次。 |
| evaluator_version | 可选精确版本。缺失、null 或整数 -1 时，评测准备选择 PyPI 最新可用稳定发行，冻结后用于本轮续接。 |
| subset_list | 可选非空唯一子集数组。缺失、null 或整数 -1 选择该版本任务实际声明的全部评测子集。每个所选子集使用全量原题。 |
| task_name、task_revision | 可省略。`task_name` 如填写，须与 `dataset` 和 EvalScope 原生注册名称一致。`task_revision` 须与注册任务的 evaluation_version 一致，任务未提供该值时采用实际 EvalScope 发行版本。适配器源码摘要随证据保存。 |
| prompt_config | 可声明 request_format、enable_thinking、system_prompt、prompt_template、chat_template。request_format 可填 `chat` 或 `completion`。当前 EvalScope 质量复测只实现 `chat`，填 `completion` 可通过输入校验，准备复测时会报告不支持。原生 few-shot 数和提示来自固定版本的评测任务。 |
| generation_config | 可声明 temperature、top_p、top_k、max_tokens、seed、stop。stop 接受字符串或非空字符串列表。基线侧的显式值用于复测；其余采用可核验的原生值。原生未指定 max_tokens 时，NPU 按服务实际策略执行，预检使用剩余上下文作为上界。 |
| scoring_config | 自定义评分规则可填写 scorer_name、scorer_version、answer_extraction、normalization、invalid_answer_policy。每项为文字，Agent 负责核对声明与实际评分器及原生配置的映射。 |

`gpu_baselines` 缺失、null、整数 -1 或空数组时，仅在任务包含精度评测意图的情况下按下节流程搜索模型的公开 GPU 基线。客户明确提供基线时，每份基线的 `scores` 必须有成绩。工具或数据版本的自动补齐仅描述本次 NPU 重跑条件。历史 GPU 使用的条件由已有历史资料另行核验。

## 客户未提供 GPU 基线

Agent 用顶层 `model.model_repository_id` 查询 Hugging Face 发布仓库。仅有 `model.model_name` 时，优先采用已与部署权重核对的来源仓库。还没有仓库依据时，按模型名检索同名候选，逐个检查模型卡和评测资料，用 GPU 运行记录与当前权重核对。多个仓库都匹配且无法唯一归属时，记为未评估。搜索回执保留实际访问的来源和检查范围。

模型卡和评测附件按不可变提交读取。搜索包括模型仓内的评测 JSON、模型卡指定的报告文件和相关评测仓库。Agent 也可在内部 `observations.quality_baseline_sources` 提供额外的 HTTPS 附件地址，限模型仓或其官方模型卡明确链接的 GitHub、Hugging Face 发布仓库。[Hugging Face 模型卡和评测结果](https://huggingface.co/docs/hub/model-cards)

来源须能绑定到已完成的原始 GPU 运行。支持带配置、v2 评分报告、同轮原始清单的 EvalScope GPU 归档，以及清单能唯一映射到配置和原生报告的一组发布文件。原始文件通过 SHA256 重定位，保留模型卡、下载地址、原始运行记录和目录映射。来源有分数表或 `model-index` 但缺少 GPU 设备、当前模型权重身份、数据和样本范围、原生配置、评分依据或影响结果的静态服务条件时，质量对齐直接输出 `not_evaluated` 和缺项。其他迁移或普通评测任务独立继续。

材料齐全时，工作流从已核验的 GPU 归档取得评测器版本、数据集 commit、子集和每项原生指标的映射，按该 commit 取得官方题库并核对字节摘要。NPU 根据实际准备的数据和测量配置生成独立协议，再与 GPU 协议及原生总分、分项核对。同一公开基线有任何协议内的指标或子集分数无法核验时，记录该项原因并跳过这份基线，其他完整基线仍可评测。自动搜索不要求逐题成绩。发行方可在同轮原始 GPU 运行记录 `source.scores[]` 中写明 `metric`、可选 `subset`、`gpu_score` 和 `target.max_drop`。范围与原生 GPU 分数一致且目标唯一时，质量报告按此允许下降量判定，并标注发布者及原始文件摘要。无可核验容差时仍输出有效分差，目标状态为未评估。搜索回执记录原始 URL、资料摘要、缺项及网络错误。同一任务续接校验已固定材料并复用搜索结果。

## 每条成绩与明细

- `subset` 指定当前范围内的单个子集。省略时按所选范围比较原生总分。
- `target` 支持 `{"max_drop": 非负数}`。边界相等时达标。单位沿用指标原生单位，例如准确率以 0 到 1 表示时，0.005 对应 0.5 个百分点。缺失、null 或整数 -1 时目标记为未评估，已证实可比的成绩仍输出分差。
- `sample_results` 中每条提供 `sample_id` 和 `score`。跨子集可补充 `subset`，单子集继承所属分项。题号使用原文件的字符串或整数 ID；没有显式 ID 时使用“相对源文件名#零基数据行号”。布尔值仅作原生逐题评分。缺失、null 或整数 -1 表示没有逐题比较请求。显式空列表、重复或未配对的题号影响逐题证据，仍保留有效的分数比较。
- 对显式填报的 GPU 基线，报告核验历史工具版本、来源、分数、样本和评分条件。仅有填报分数时，可保留有效的 NPU 成绩，目标判定为 `not_evaluated`。更改 GPU 分数、容差或希望查看的题号时，优先使用满足同一测量协议的已有 NPU 成绩。所选范围的原生报告保留实际可得的总分与分项；粗判阶段使用评分证据，提示预检只汇总长度。

## Agent 的准备观察

客户提供上述业务字段。C-Eval 默认从仓库登记的 ModelScope 数据集发布包按 commit 取原始 CSV，GSM8K 从原作者 GitHub 仓库按 commit 取 train/test 原始 JSONL。其他数据集由 Agent 查证相应官方来源。Agent 在运行目录外收集资料，在内部 `observations.quality_sources[dataset]` 放置解析资料对象或其 `{path,sha256}` 引用。原生目录、GPU 历史材料、官方索引正文和数据准备请求的文件身份均由 Agent 维护。

| 内部材料 | 内容 |
| --- | --- |
| repository | 原始发行方的 `https://github.com/组织/仓库` 、`https://huggingface.co/datasets/组织/仓库` 或 `https://www.modelscope.cn/datasets/组织/仓库`。按评测器的 dataset/变体核实来源；仓库元数据或提交地址不足以核对本地题库内容。 |
| snapshot_binding | 已核验的官方文件清单引用。含 repository、revision、files（源文件相对路径到 SHA256）。上游路径不同可填 upstream_paths 对照，ZIP 格式可填 archive，执行时根据不可变 revision 的官方 URL 核对实际源字节。 |
| preparation | 数据准备 Skill 的公开请求。使用完整原始数据。基线按实际 eval_split 重建选择，可继承 observations.datasets 同数据集的准备配方。对于通用数据另用 subsets 中的 name、split、source_file 映射原题。 |
| evaluator_environment、catalog | 可选。前者为实际解释器 python 和版本 version，省略时复用版本匹配的现有评测环境。版本不匹配时通过公开准备入口在本基线运行目录的独立 venv 中安装选定版本。通常直接用该解释器读取 EvalScope 原生注册任务的子集、元数据和默认生成参数。若提供离线原生 catalog，其固定引用须指向同版本的 evalscope-quality-catalog-v1 记录，并核对本地 adapter 源码摘要。 |
| metrics | 指标配置含 selector（name、aggregation、dimensions）、unit、direction、sample_metric、sample_kind。单一原生准确率可根据注册指标映射；其他指标或同名变体由 Agent 用固定版本原生证据显式绑定。 |
| 评分/变体 | task_args 可映射原生提示或评分设置。自定义评分提供 scoring_proof 固定引用，含 declaration、evalscope_version、benchmark、adapter_sha256、scoring_config。实际原生运行应复核评分器源码和配置。 |
| 现有 GPU 历史资料 | 在已有 context.evidence 中传原生质量回执或 evalscope-gpu-history-v1 归档索引引用。多份资料时优先使用唯一匹配的冻结协议。报告独立核验历史指标、样本和 GPU 身份。 |

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
