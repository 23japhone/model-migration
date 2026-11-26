# 客户输入与三文件完成合同

客户用 prompt 说明部署和评测意图，用 `migrate.py --task task.json` 提交参数。可复制[普通任务模板](../assets/template.json)、[含 GPU 基线的质量评测示例](../assets/task-quality.example.json)或[三层精度对齐完整示例](../assets/three-level-alignment.example.json)，按本次任务替换示例值。Agent 准备的 context 另见[执行与续接](customer-observations.md)。客户入口与旧 `--request` 互斥。

## 字段

最小合法结构是 `{"model": {}, "environment": {}, "accuracy": {}}`。三个对象均必填。`model` 标识模型和权重，`environment` 限定部署环境，`accuracy` 选择普通评测和精度对齐条件。各组内可按下表省略字段。静态计划可接受三个空对象，实际部署仍须能确定模型、镜像和可用设备。入口拒绝未知字段、重复 JSON 键，以及旧字段 `served_model_name`、`vllm_ascend_image`、`evaluator_python`、`deployment`。

对支持“未指定”的字段，省略、`null` 和 JSON 整数 `-1` 等价。`-1.0`、字符串 `"-1"`、布尔值和空白字符串均不能作为缺省标记。嵌套对象中的必填项仍需给实际值，例如 `gpu_score: 0` 表示真实零分。设备、普通评测数据集和子集列表显式填写时须非空。路径指执行主机上的绝对路径，可含空格，禁止包含 `..`。性能 Skill 输入记录器的缺省写法另按其自身契约。

入口先校验原始 JSON 和显式值，再用 prompt 中的明确要求补齐未指定字段。下表的缺省行为适用于这两处都未指定的情况。双方显式值冲突时集中确认。Agent 的 `prompt_fields` 使用两层字段路径，数据集条目按名称和顺序合成。环境前置检查、路径保护和业务执行共用一次合成结果。流程保留原始输入并冻结实际选择。已开启张量对齐的任务调整容差时，见[输出与恢复](#输出与恢复)。

### 模型、环境与普通精度测试

| 字段 | 含义及格式 | 是否必填 | 缺省行为 |
| --- | --- | --- | --- |
| `model.model_repository_id` | 模型来源，格式为 `namespace/repository` | 找不到完整本地权重时必填 | 复用核验完整的本地模型。若同时给出 ID 和本地路径，还会核验模型身份。 |
| `model.path` | 本地模型权重目录 | 否 | 查 ModelScope 和 Hugging Face 的实际缓存。多个可用快照时须指定路径。需下载时写入所选来源缓存根下的 `customer-models/<仓库ID>`。 |
| `model.model_name` | 任务记录、结果展示和 API 调用共用的 vLLM 服务名 | 无法从模型解析时必填 | 依次取已核验的来源仓库 ID、配置中的仓库 ID、模型类型或唯一架构名。 |
| `environment.image_name` | 部署镜像的 tag、digest 或镜像 ID | 否 | 从官方 Quay 选最高基础版本，冻结标签和目标平台 digest。变体须显式指定。见[缺省镜像选择](#缺省镜像选择)。 |
| `environment.container_name` | Docker 容器名，最长 101 个字符 | 否 | 优先用 `vllm_<最终模型名>`，必要时规范化并附加稳定哈希。 |
| `environment.available_npu_devices` | 本任务允许使用的物理 NPU ID，须为非负整数且无重复 | 否 | 将主机实际发现的全部 NPU 纳入候选池，再按容量和可用性选卡。 |
| `accuracy.evaluator` | 普通精度测试工具，可填 `evalscope` 或 `aisbench`，大小写不敏感 | 否 | 使用 `evalscope`，准备时绑定实际版本。显式选 AISBench 可执行精度测试，当前三文件交付仅支持 EvalScope。 |
| `accuracy.datasets` | 普通精度测试的数据集数组，名称唯一，按声明顺序执行 | 否 | 依次生成 `ceval` 和 `gsm8k`。显式选择时只使用列出的数据集。 |
| `accuracy.datasets[].name` | 原生评测工具中的数据集标识 | 每个显式条目必填 | 无默认名称。 |
| `accuracy.datasets[].path` | 执行主机上的数据集目录 | 除 `ceval`、`gsm8k` 外必填 | `ceval` 使用 `/data/ceval`，`gsm8k` 使用 `/data/gsm8k`。 |
| `accuracy.datasets[].subset_list` | 普通测试要测的子集名数组，名称唯一 | 否 | 取该版本原生 adapter 声明的全部实际子集。 |
| `accuracy.datasets[].limit` | 每个所选子集取前 N 题，N 为正整数 | 否 | 每个子集取全量，归一化后为 `null`。该上限仅用于普通精度测试。 |

默认模型名依次取当前资产绑定的来源/身份复核仓库 ID、实际 config.json 中符合 namespace/repository 格式的 _name_or_path、非空 model_type、唯一 architectures 项。配置文件须与模型回执中的文件摘要及 metadata 一致。只有本地路径的 _name_or_path 不采用；来源 ID 冲突先处理冲突，不以架构兜底掩盖。架构名称和配置名称只作为服务别名，不反向写入 model_repository_id，也不证明仓库身份；所有候选不可用时要求明确 model_name。

默认容器名先尝试 vllm_ 加模型名，合法时保持原样；否则将连续非法字符替换为 -，去掉两端替换分隔符，空结果用 model，截取最多 83 字符，再加连字符及原模型名 SHA256 前 12 位。显式容器名只校验、不改写。模型服务/API 名称原样保留，不套容器名规则。

默认数据目录已有完整数据先校验复用，缺失时按现有官方来源准备；权限不足或已有内容冲突时阻塞，不静默换目录。显式只选择一个数据集时不追加另一项；显式列表中的 name 不允许缺失/null/-1，不按列表位置猜测。

### GSM8K 子集与题数

客户 EvalScope 流程使用数据准备生成的原生目录：GSM8K 的 README 声明唯一 main 配置并设为默认，明确映射 train.jsonl/test.jsonl。subset_list=["default"] 通过原生加载器选择默认配置，["main"] 明确选择 main；整数 -1、null 或缺省不覆盖 adapter 的子集声明，当前 GSM8K 展开为 main。三种选择加载同一份测试文件，保留各自显式选择及回执名称，不在输入中互换名称；default/main 不应作为两个不同题集重复评测。

subset_list 的 -1 不控制题数。limit 为正整数 N 时取 test.jsonl 的前 min(N, 实际行数) 条，缺失/null/整数 -1 时为完整测试文件；train 和 socratic 文件不计入测试题数。题数以本次准备和原生预检实际记录为准，不写死为官方样本数。旧目录需重新准备并绑定新回执，不能用新规则改写历史证据。

### 任务质量对齐与 GPU 基线

| 字段 | 含义及格式 | 是否必填 | 缺省行为 |
| --- | --- | --- | --- |
| `accuracy.gpu_baselines` | GPU 历史评测基线数组，每项对应一套评测协议和成绩 | 否 | 省略、`null`、整数 `-1` 或 `[]` 均表示未提供基线。有精度意图时按模型查公开基线，材料不足记为 `not_evaluated` 并继续其他任务。仅要求性能且无基线时记为 `not_requested`。 |

下表字段均相对于一项 `accuracy.gpu_baselines[]`。用户显式提供基线时，最少填写 `evaluator`、`dataset` 和含实际分数的 `scores`。要判定达标，还须核验历史 GPU 成绩、两端评测条件与目标。自动选择的工具版本只描述本次 NPU 复测条件。详见[任务质量的证据要求](task-quality.md)。

| 字段 | 含义及格式 | 是否必填 | 缺省行为 |
| --- | --- | --- | --- |
| `evaluator` | GPU 使用的评测工具。当前质量复测支持 EvalScope | 是 | 无默认值，须独立于普通测试的 `accuracy.evaluator` 声明。 |
| `dataset` | GPU 评测使用的原生 benchmark 标识 | 是 | 无默认值，须按 GPU 实际使用的数据集填写。 |
| `scores` | 总分或分项分数组，同一指标与子集的组合唯一 | 是 | 至少填一条，零分可填 `0`。 |
| `evaluator_version` | GPU 使用的评测器精确发行版本 | 否 | 手填基线省略版本时，NPU 复测选择 PyPI 最新可用的 EvalScope 稳定版，本次任务续接沿用冻结版本。 |
| `subset_list` | 基线覆盖的子集名数组，显式填写时须非空、唯一 | 否 | 取该版本原生任务声明的全部评测子集。每个所选子集用全量原题，基线对象不接受 `limit`。 |
| `task_name` | GPU 使用的注册任务名，须与该 benchmark 的原生任务一致 | 否 | 从固定版本的原生注册信息取得。 |
| `task_revision` | 原生评测任务实现版本 | 否 | 取该任务的 `evaluation_version`，缺失时取实际 EvalScope 发行版本。 |
| `prompt_config` | 提示词、聊天模板和请求形式 | 否 | 使用固定版本的原生任务和服务模板，质量复测走 `chat`。 |
| `generation_config` | GPU 生成答案时的解码配置 | 否 | 使用该版本的原生生成配置，显式给出的参数逐项覆盖。 |
| `scoring_config` | GPU 自定义评分器和规则声明 | 否 | 使用原生规则评分、过滤和聚合。显式声明须能与实际评分器及其配置对应。 |

**每条成绩。** 以下字段相对于 `accuracy.gpu_baselines[].scores[]`。

| 字段 | 含义及格式 | 是否必填 | 缺省行为 |
| --- | --- | --- | --- |
| `metric` | 原生指标名，如 `accuracy` | 是 | 无默认值。 |
| `gpu_score` | 该范围与指标下的 GPU 实际成绩，按原生单位填写有限数值 | 是 | 无默认值。 |
| `subset` | 这条分数所属的单个子集 | 否 | 使用所选范围的原生总分。 |
| `target` | 该指标允许的退化量，格式为 `{"max_drop": 数值}` | 否 | 可比时仍报告 `NPU − GPU` 分差，目标状态为 `not_evaluated`。 |
| `target.max_drop` | 按指标好坏方向允许的最大退化量，有限非负数 | 给出 `target` 时必填 | 无默认容差。退化量等于阈值算达标，`0` 表示不允许变差。对 0～1 准确率，`0.005` 代表 0.5 个百分点。 |
| `sample_results` | 请求逐题比较的 GPU 评分明细数组 | 否 | 省略表示不请求逐题核对。显式 `[]` 会把逐题项记为未评估，保留有效的总分或分项分。 |
| `sample_results[].sample_id` | 原始题号，字符串或整数 | 每条明细必填 | 无默认值。原题没有显式 ID 时，填写“相对源文件名#从零开始的数据行号”。 |
| `sample_results[].score` | 此题的 GPU 原生得分，二元评分也可用布尔值 | 每条明细必填 | 无默认值。 |
| `sample_results[].subset` | 此题所在的子集 | 题号无法唯一配对时必填 | 优先继承所属分项的 `subset`。总分下按题号匹配，跨子集重号时须明确子集。 |

**提示、生成与评分参数。** 以下字段相对于一项 `accuracy.gpu_baselines[]`。三个配置对象内部的字段均可省略。要继承原生配置，直接省略相应键，显式值须符合各自类型。

| 字段 | 含义及格式 | 是否必填 | 缺省行为 |
| --- | --- | --- | --- |
| `prompt_config.request_format` | 请求形式，可填 `chat` 或 `completion` | 否 | 默认 `chat`。`completion` 可通过静态校验，当前质量复测执行时报告不支持。 |
| `prompt_config.enable_thinking` | 是否启用模型思考模式，布尔值 | 否 | 沿用原生生成配置或服务模板生效的设置。 |
| `prompt_config.system_prompt` | 系统提示词文本 | 否 | 沿用原生任务配置。 |
| `prompt_config.prompt_template` | 把题目组成评测提示的模板文本 | 否 | 沿用原生 benchmark 模板。 |
| `prompt_config.chat_template` | 把聊天消息格式化为模型输入的模板文本 | 否 | 使用服务实际生效的聊天模板。 |
| `generation_config.temperature` | 采样温度，有限非负数 | 否 | 沿用原生生成配置。 |
| `generation_config.top_p` | 核采样累积概率，范围为 `(0, 1]` | 否 | 沿用原生生成配置。 |
| `generation_config.top_k` | 采样候选数，整数 | 否 | 沿用原生生成配置。 |
| `generation_config.max_tokens` | 最大生成 Token 数，正整数 | 否 | 优先用原生预算。若原生也未设上限，推理沿用服务实际策略，预检以剩余上下文为上界。 |
| `generation_config.seed` | 随机种子，整数 | 否 | 沿用原生生成配置。 |
| `generation_config.stop` | 停止字符串或非空字符串列表 | 否 | 保留原生停止条件。 |
| `scoring_config.scorer_name` | 评分器名称 | 否 | 使用原生评分器。 |
| `scoring_config.scorer_version` | 评分器版本标识 | 否 | 随本次绑定的原生评分实现。 |
| `scoring_config.answer_extraction` | 从回答中提取待评分答案的规则 | 否 | 沿用原生答案提取规则。 |
| `scoring_config.normalization` | 评分前对答案做标准化的规则 | 否 | 沿用原生标准化方法。 |
| `scoring_config.invalid_answer_policy` | 无效或无法解析的答案如何计分 | 否 | 沿用原生评分器的处理规则。 |

### Token 预测对齐与张量对齐

下表字段均位于 `accuracy` 下。有精度评测意图或提供了 GPU 基线时，流程进入任务质量对齐。第二遍从质量报告中选择可比且已确认未达标的题目或子集。第三遍需要同题、同前文且两端都有预测的首个异常 Token，也可续接与本次质量报告匹配的外部 Token 报告。当前定向采集适用于单卡、同步 Eager、文本贪心生成。GPU 预测包、共同采集清单和张量包由 Agent 通过 `context.evidence` 交接。见[Token 选题与续接](task-quality.md#从未达标分项进入-token-预测对齐)和[第三遍采集](../skills/vllm-precision-align/references/tensor.md)。

| 字段 | 含义及格式 | 是否必填 | 缺省行为 |
| --- | --- | --- | --- |
| `token_alignment` | 启用第二遍，比较同一前文下逐步预测的全词表分数与实际接受的 Token ID | 否 | 省略、`null` 或整数 `-1` 表示关闭。启用时须同时声明下面两项容差，`{}` 无效。 |
| `token_alignment.raw_logits.atol` | 全词表原始 logits 允许的最大绝对差 | 开启第二遍时必填 | 无默认值。有限非负数，`0` 要求逐候选相等。 |
| `token_alignment.processed_scores.atol` | 选词前、经规则处理并归一化后的全词表概率允许的最大绝对差 | 开启第二遍时必填 | 无默认值。有限非负数，等于容差仍通过。两端选中同一 Token 时，分数超限也会报告异常。 |
| `tensor_alignment` | 启用第三遍，比较首个可比异常位置的模块和算子前向张量 | 否 | **省略、`null` 或整数 `-1` 表示关闭第三遍。显式 `{}` 表示开启**，报告有限浮点误差及其坐标，浮点数值无达标阈值。整数、布尔值、结构和非有限值仍按比较契约核对。 |
| `tensor_alignment.atol` | 浮点张量逐元素比较的绝对容差，有限非负数 | 指定浮点阈值时与 `rtol` 同时必填 | 无默认值，单独提供一个容差会被拒绝。 |
| `tensor_alignment.rtol` | 相对于 GPU 参考值幅度的容差，有限非负数 | 指定浮点阈值时与 `atol` 同时必填 | 无默认值。两项都填 `0` 时要求有限浮点元素数值相等。 |

双端浮点阈值按元素计算，满足 `abs(NPU − GPU) ≤ atol + rtol × abs(GPU)` 即通过。在客户回执 `customer-manifest.json` 中，任务质量结论见 `task_quality_conclusion.precision_target`，首个异常 Token 见 `token_alignment[].token_report` 引用的报告，张量误差及坐标见 `tensor_alignment[].tensor_report` 引用的报告。

如果先取得一端的预测包，可以先交给 Agent，再沿用同一运行补交另一端。只开启 Token 对齐也支持这种续接。两次提交须对应同一份质量报告和相同的 Token 容差；可在 `token_alignment[].stage` 引用的回执中核对实际使用的预测包。

### 三层精度对齐完整示例

将[完整 JSON 示例](../assets/three-level-alignment.example.json)复制到 Skill 目录外作为 `task.json`。示例选择一张 NPU，普通精度测试取 C-Eval 的 `accountant` 子集前 10 题。第一遍任务质量复测则使用该子集的全量原题。示例声明 GPU 准确率为 `0.75`，允许下降 `0.005`，因此在两端条件可比时，NPU 准确率达到 `0.745` 即达标。实际使用时请替换模型、设备、GPU 实测成绩、生成条件和约定容差。

若该子集可比且未达标，第二遍按题比较 logits 和概率，缺 GPU 预测包时先交付选题清单。发现可比的首异常后，第三遍按示例中的 `"tensor_alignment": {}` 记录张量误差。若需判定有限浮点元素是否超限，将该对象改为一对实际约定的 `atol`、`rtol`。

将 `MIGRATION_SKILL_ROOT` 设为完整 Skill 的安装目录，在存有 `task.json` 的包外目录执行以下静态检查。合法输入返回 `status=not_run`。

```bash
python3 -B "$MIGRATION_SKILL_ROOT/scripts/migrate.py" --task task.json --work-root ./runs --run-id alignment-example --plan
```

### prompt 补齐数据集字段

仍使用 `prompt_fields["accuracy.datasets"]` 列表，不接受 `accuracy.datasets.0.limit` 等索引路径。JSON 与 prompt 都提供列表时，name 序列必须完全一致，包括数量和顺序；增加、删除、改名或重排集中报告选择冲突。每个 prompt 条目必须有 name，不修改的项只写 name，不能省掉该项。

在对应条目内逐字段处理 path、subset_list、limit：一边缺失/null/整数 -1、另一边明确时采用明确值；双方明确且相同则接受，不同则报告含数据集名称和字段的冲突。prompt 未指定项不清空 JSON 的明确要求。subset_list 是一个完整字段，不拼接、求并集或重排；空列表及非法显式值仍拒绝。校验部分 prompt 条目时可以省略 JSON 已有的自定义数据集路径，但原始 JSON 本身缺少必需路径仍无效，不能靠 prompt 修补。

例如 JSON 已按顺序选择 ceval、gsm8k，ceval 的 limit 和 subset_list 未指定，Agent 可生成：

```json
{
  "prompt_fields": {
    "accuracy.datasets": [
      {"name": "ceval", "limit": 20, "subset_list": ["computer_network"]},
      {"name": "gsm8k"}
    ]
  }
}
```

结果保留原路径、顺序和 gsm8k 要求，只补齐 ceval 的两项；JSON 若已明确 limit=100，则与 20 冲突，不覆盖。JSON 整组 datasets 未指定时，prompt 列表就是完整选择，不追加默认项；两边都未指定才生成默认 ceval/gsm8k。

合成后才应用静态默认和路径检查。原始 task/inputs 不变，声明、数据准备、原生 catalog、精度请求和交付使用同一实际选择。续接仅允许不同写法得到相同的冻结声明；改变已冻结路径、limit 或子集须新任务，不复用旧 catalog/精度结果。冲突诊断中的索引只用于定位，不代表新增 prompt 路径接口。

### 计划、模型与镜像身份

--plan 仅做 JSON 结构校验与静态补值，status=not_run；uncovered 列出待解析的模型名、容器名和设备池，并明确尚未合成 prompt。计划不读取 context，预览中的后端和默认参数不是最终执行选择。不读取 config、不探测 NPU、不安装或下载，也不创建运行目录。实际解析失败仅阻塞依赖消费者；独立准备继续。

`model_repository_id` 表示模型来源与身份，`path` 表示本地位置，二者不能混淆。两者同时提供时，先核验本地权重完整性，再核对本地模型与指定仓库的身份一致性；自动发现的本地缓存同样须核对。已有完整权重在身份一致后优先复用，不因进入流程就重新下载，也不未经允许覆盖。身份冲突时向用户集中确认实际模型；身份不明时由 Agent 继续查来源证据，只有无法可靠确定的必要信息才询问用户。不能以本地完整为理由忽略仓库 ID，也不能仅凭 model_name 或目录名认定模型存在或身份一致。

模型发现只负责完整性与来源候选。完整本地模型且显式给出仓库 ID 时返回 `local_identity_review_required`；Agent 按[身份复核契约](customer-observations.md#模型身份复核)提交绑定仓库 ID、实际路径、文件清单摘要和实际来源证据的复核。只有 `match` 才能进入模型准备、迁移及后续精度；`conflict` 保留资产并请求用户决策，`unknown` 或缺证据保持阻塞，独立数据准备继续。仓库 ID=-1 时允许核验并复用本地模型，但不声称已核对上游仓库身份。

缺权重且仓库 ID=-1 时要求补充；明确仓库 ID 先查 ModelScope，确认不存在才查 Hugging Face。网络、鉴权和未知 SDK 错误保持原类别，不能当作不存在；只有两边均确认不存在才提示更正 ID。已有部分文件不覆盖其冲突内容，远程准备核对后补齐缺失文件。

客户名称进入 vLLM 的 `--served-model-name`、功能验证及精度请求的 API `model`，并保存在客户记录中；下游 vLLM 配置的 `served_model_name` 等原生字段不改名。旧 task 和旧结构回执拒绝续接；请按新模板建立新任务。原输入和历史回执不回写，不继承“本地权重覆盖仓库 ID”的旧结论。

### 缺省镜像选择

当 `environment.image_name` 缺失、为 `null` 或整数 `-1`，且 prompt 也未指定镜像时，正式流程从[官方 Quay 镜像仓库](https://quay.io/repository/ascend/vllm-ascend?tab=tags)分页读取有效标签。仅比较 `vX.Y.Z`、`vX.Y.Z.postN`、`vX.Y.ZrcN` 三种基础镜像。先按数字选最高的 `X.Y.Z`，再在这一版本内依次优先最高编号的修订版、正式版、最高编号的候选版。例如，同时存在 `v0.30.0`、`v0.31.0rc2` 和 `v0.31.0rc10` 时选 `v0.31.0rc10`；新任务在 `v0.31.0` 发布后选正式版，随后有 `v0.31.0.post1` 时选修订版。

需要 A3、310P、openEuler 等变体时显式填写完整镜像引用。模板中的具体 tag 也是显式选择；希望使用上述默认值时可删除该字段。Agent 根据选中镜像、目标硬件和官方资料核验配方及兼容性。

同一任务续接沿用已选标签，已取得目标平台 digest 后也固定该 digest。目标 Docker 中匹配的镜像可直接复用，确认缺失才按 digest 拉取。标签列表不完整、平台无法唯一匹配或已有同名镜像身份冲突时，该步报告阻塞或失败，保留现场，不自动降级。

### EvalScope 环境准备

客户 JSON 不增加安装配置。每次新任务或续接进入准备时，由公开准备操作调用随包原版 install_evalscope.py，默认安装到主机共享 /data/evalscope-venv。Agent 可在可选 context.evalscope_install 中传 python、venv、version、upgrade、dry_run、help；完整参数、来源摘要和依赖见[共用环境安装契约](evalscope-installation.md)。AISBench 与该对象同时提供时拒绝，不切换后端。

只有退出码 0、success 且返回环境与配置一致才就绪；记录实际 Python、EvalScope 版本和 CLI，精度请求不另查 latest。帮助或预览返回 not_run 并停止业务执行。超时、失败或结果不一致保留日志，不自动重试或清理部分安装，独立资产步骤继续。

续接省略安装配置时继承原基础 Python/venv，首次成功后固定实际 EvalScope 版本；操作开关不继承。先检查配置变化和已有绑定环境，再调用安装脚本：显式换环境要求新 run-id，不修改原绑定；确认实际环境变化则使绑定永久失效，不先安装恢复旧版本。未知探测失败保留绑定和诊断。历史原版安装回执只有一致的请求/成功结果/绑定才能恢复选择，更早 selection 或证据不足须新任务。精度 API、配置、数据、tokenizer、服务和原生结果仍在各消费者核验，perf 帮助通过不能替代这些检查。

本地模型解析与评测环境独立。完整本地权重不要求 ModelScope/Hugging Face SDK；远程解析明确报告缺少资产 SDK 时才另建 SDK 环境，不向共享评测环境补装资产 SDK。资产 SDK 与评测回执分别记录；未知 SDK 错误仍阻塞。

同名容器的观察明确为 `status=exited` 且 `running=false` 时，自动通过 verify-agent 公开入口再次核对完整 container_id/image_id/started_at、名称和退出状态，按完整 CID 非强制删除，再继续正常重建，无需额外用户授权。身份或状态变化、删除失败均阻塞，不改用强制删除。其他状态仍须显示精确身份并取得删除重建授权；详见[同名容器替换](customer-observations.md#同名容器替换)。

`available_npu_devices` 限定候选设备。省略时按[完整设备观察](customer-observations.md#默认设备池)发现候选池，显式填写时仅在所列设备中选择。流程结合权重、上下文、KV 布局、运行开销和版本支持的 TP 优先选满足容量的空闲组合，容量不足时再评估[共享部署](customer-observations.md#空闲优先与共享部署)。设备观察不完整、零设备或容量不足时阻塞。部署入口在启动前再次核对实际设备。

服务地址由内部部署策略生成：监听本机 `127.0.0.1`，从端口 `8000` 起逐个检查空闲端口，到 65535 仍不可用则阻塞。实际 host/port 写入冻结请求及交付文件，不再属于客户 JSON 或 prompt_fields。

## 精度方案

普通精度测试固定 few_shot_num=0、temperature=0、top_p=1、seed=42、eval_batch_size=1、max_tokens=8192，stream=false、n=1、retries=1、model_args.max_retries=0、rule judge。支持 enable_thinking 时关闭，不支持时记录能力。用原生 loader 得到实际样本并以本模型 tokenizer 计算 prompt 长度；上下文不足明确阻塞，不截断输入、不降低 8192 输出预算。

每个数据集/子集生成独立评测项，使用真实原文件前 min(N,行数) 或全部样本身份。不得自改原生模板、答案解析或评分器。多个数据集完整汇总，同一数据集的子集在条件相同后合并到交付对象。输入端重复数据集名称拒绝。

未知数据源由 Agent 查找官方发布与当前固定工具的原生 adapter，补齐配方及证据；实际来源存在歧义才询问。仍保留现有标准文本 adapter、原始 JSON/JSONL/CSV 及规则评分能力边界；超出已实现原生映射的任务明确阻塞，不能转换数据或伪造样本映射以绕过核验。

显式 AISBench 在执行前告知三文件交付暂不支持；可生成其原生配置并执行精度，不能自动改成 EvalScope。其最终客户回执 `delivery_supported=false`、完整交付 blocked，原精度结果保留。

## 自动负载 customer-baseline-v1

输入和输出各最多 1024 tokens，二者之和不超过实际服务上下文。并发为 1、2、4……，上限为 min(64、max_num_seqs、该负载 KV 可用 token 容量除以总 token 数)，仅单并发可行时为 [1]。KV 采用固定架构和当前硬件的保守预算，实际服务参数不得小于计划上限；Agent 应据服务证据收紧估算，不能把设备张数直接作为并发。

请求数=max(32,4×最大并发)，预热=min(8,最大并发)，seed=42，enable_thinking=false，ignore_eos=true，单请求超时600秒、单轮3600秒。运行指标按真实可用性开启，采样1秒、采集超时10秒。配方及容量依据保存在内部冻结观察和分配回执；这些是初始负载参数，不证明性能最优或目标达标。

## 输出与恢复

```text
work-root/run-id/
├── customer-workflow/                 # 内部冻结请求、尝试台账、独立导出回执
└── deliverables/
    ├── benchmark_plan.json
    ├── accuracy_plan.json
    └── deployment.json
```

候选来自真实完整 entry、部署 argv、有效 EvalScope 冻结配置及实际数据身份。deployment.model_path 的实际字段为 server.model_path，使用运行载体路径；tokenizer 和数据路径在其消费主机核验。evaluation.model_path 是服务调用名称。CANN 路径在绑定当前 API 进程的命名空间检查，不用宿主机同名目录代替。

全部校验后原子重命名整个交付目录，独立 export-manifest.json 标记 ready 后才能 completed。已有同内容三文件可幂等复用；不同内容拒绝覆盖。写入失败不交付半套；若目录已发布但回执失败，不能宣告成功，续接导出重新核验并补成独立回执。

`--resume` 绑定原 task.json、run-id 与原回执。通常要求输入文件摘要一致。原任务已声明 `accuracy.tensor_alignment` 时，可在原路径只修改该对象的容差，并更新 `context.task_source` 摘要。已交付三文件且已有单题 Token 报告时，可按[第三遍续接约定](../skills/vllm-precision-align/references/tensor.md#续接与报告)复用上游证据。首次为已有任务启用该字段需使用新 run-id，或直接向精度阶段提供已冻结的报告。

进入常规准备时，已准备文件重新核对摘要，已执行配方冻结，环境继承原选择。每次有效正式准备仍调用安装脚本，安装前后核对实际 Python 与 EvalScope 版本绑定。迁移和评测原始引用均保留；有效评测通过公开 analyze 只读复核后复用。导出重试不部署、不重新评测；发布前仍重新核验完整来源。

退出码：0=计划/安装帮助/安装预览有效或完整交付完成；2=客户字段/顶层引用错误；3=依赖、人工决策、语义复核、AISBench 交付边界或其他未完成；1=执行/证据失败或取消。以 status、steps 和独立回执共同判断，计划 not_run 不表示执行成功。资源状态以原始 verify 回执及本次观察为准，历史 retained 不等于当前存活。

客户汇报首句取自 `task_quality_conclusion.summary`。自动化确认任务质量达标时，检查 `task_quality_conclusion.precision_target` 是否为 `pass`。[客户回执中的任务质量结论](task-quality.md#客户回执中的任务质量结论)
