# 客户输入与三文件完成合同

本契约对应 `migrate.py --task task.json`，与旧 `--request` 互斥。自然语言由 Agent 理解；脚本不靠关键词决定意图。完整输入模板见[template.json](../assets/template.json)，内部观察与执行步骤见[customer-observations.md](customer-observations.md)。

## 字段

客户 JSON 只接受 `model`、`environment`、`accuracy` 三组；三组必须存在且为对象，允许空对象。普通精度测试字段见模板。可选的 accuracy.gpu_baselines 用于任务质量对齐，按下表及专项契约填写。未知字段、重复 JSON 键，以及旧 `served_model_name`、`vllm_ascend_image`、`evaluator_python`、`deployment` 均拒绝。完整显式模板继续可用；模板中的具体模型和设备值不作为默认。

下表的“未指定”统一指字段缺失、null 或整数 -1。-1.0、true/false、空串、纯空白及字符串 "-1" 均不能替代；显式空设备列表、空数据集列表和空子集列表也拒绝。绝对路径可含空格；不能包含 `..`。路径代表实际执行主机，模型缓存中受控的官方 blobs 链接按专门规则核验。迁移保留 -1 写法不意味着性能 Skill 的输入记录器也支持该写法。

入口先校验原始 JSON 的结构和显式值，此时不生成默认。Agent 从 prompt 提取的明确参数先补齐未指定字段，再应用静态默认和运行时发现；与 JSON 显式值冲突时先集中确认。prompt_fields 保持现有两层字段语法，不增加数组叶子路径；非法 JSON 或 prompt 值直接拒绝，不能用另一来源的合法值修补。prompt 只合成一次，环境前置检查、路径保护和业务执行共享同一声明。原始 task 不回写，合成声明与实际解析结果分别冻结；续接只允许尚未解析的默认项首次完成解析，不能悄悄改变已冻结值。

| 字段 | 显式值 | 未指定时的行为 |
| --- | --- | --- |
| model.model_repository_id | namespace/repository | 仅在找到完整可用本地权重时允许 |
| model.path | 权重绝对路径 | 查实际配置的 ModelScope/Hugging Face 缓存；多候选要求明确路径 |
| model.model_name | 非空模型名称，统一用于任务记录、结果标识、展示、vLLM 服务名及 API model | 按下述模型来源与配置规则解析 |
| environment.image_name | 明确 tag/digest/image ID | 按[缺省镜像选择](#缺省镜像选择)从官方 Quay 选择最高基础版本，冻结选中的标签和目标平台 digest |
| environment.container_name | 合法 Docker 名称，最长 101 字符 | 根据最终 model_name 生成合法且稳定的名称 |
| environment.available_npu_devices | 本任务允许使用的 NPU 范围；非空、唯一、非负整数列表 | 当前执行主机实际发现的全部 NPU 候选池；不等于固定 TP |
| accuracy.evaluator | evalscope / aisbench，大小写不敏感 | evalscope；每次准备调用共用环境安装脚本，绑定实际版本 |
| accuracy.gpu_baselines | GPU 基线数组，空数组也视作未提供，详见[任务质量对齐](task-quality.md) | 有精度意图时依模型名称查找 GPU 基线，缺项时记录未评估并继续后续任务 |
| accuracy.tensor_alignment | 对象，`{}` 或两个有限非负数 `atol`、`rtol` | 以单题 Token 首异常触发共同 L0/L1 前向张量比较，结果见 `tensor_alignment[]`。[三遍交接及复算](../skills/vllm-precision-align/references/tensor.md) |
| accuracy.token_alignment | 对象，必须同时给出 `raw_logits.atol`、`processed_scores.atol` 两项有限非负绝对容差 | 省略时沿用现有质量评测流程，填写时对可比且未达标的分项定向诊断 |
| accuracy.datasets | 非空对象列表；名称唯一；显式选择及顺序保持 | 按 ceval、gsm8k 顺序生成两项 |
| accuracy.datasets[].name | 原生评测工具数据集标识 | 不允许 |
| accuracy.datasets[].path | 实际本地绝对路径 | ceval 为 /data/ceval，gsm8k 为 /data/gsm8k；其他数据集须明确路径 |
| accuracy.datasets[].subset_list | 非空、唯一字符串列表 | 展开原生 adapter 的全部实际子集 |
| accuracy.datasets[].limit | 正整数，每子集最多前 N 条 | 全量；内部和 accuracy_plan 固定为 null |

默认模型名依次取当前资产绑定的来源/身份复核仓库 ID、实际 config.json 中符合 namespace/repository 格式的 _name_or_path、非空 model_type、唯一 architectures 项。配置文件须与模型回执中的文件摘要及 metadata 一致。只有本地路径的 _name_or_path 不采用；来源 ID 冲突先处理冲突，不以架构兜底掩盖。架构名称和配置名称只作为服务别名，不反向写入 model_repository_id，也不证明仓库身份；所有候选不可用时要求明确 model_name。

默认容器名先尝试 vllm_ 加模型名，合法时保持原样；否则将连续非法字符替换为 -，去掉两端替换分隔符，空结果用 model，截取最多 83 字符，再加连字符及原模型名 SHA256 前 12 位。显式容器名只校验、不改写。模型服务/API 名称原样保留，不套容器名规则。

默认数据目录已有完整数据先校验复用，缺失时按现有官方来源准备；权限不足或已有内容冲突时阻塞，不静默换目录。显式只选择一个数据集时不追加另一项；显式列表中的 name 不允许缺失/null/-1，不按列表位置猜测。

### GSM8K 子集与题数

客户 EvalScope 流程使用数据准备生成的原生目录：GSM8K 的 README 声明唯一 main 配置并设为默认，明确映射 train.jsonl/test.jsonl。subset_list=["default"] 通过原生加载器选择默认配置，["main"] 明确选择 main；整数 -1、null 或缺省不覆盖 adapter 的子集声明，当前 GSM8K 展开为 main。三种选择加载同一份测试文件，保留各自显式选择及回执名称，不在输入中互换名称；default/main 不应作为两个不同题集重复评测。

subset_list 的 -1 不控制题数。limit 为正整数 N 时取 test.jsonl 的前 min(N, 实际行数) 条，缺失/null/整数 -1 时为完整测试文件；train 和 socratic 文件不计入测试题数。题数以本次准备和原生预检实际记录为准，不写死为官方样本数。旧目录需重新准备并绑定新回执，不能用新规则改写历史证据。

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

`available_npu_devices` 是允许使用的范围，并非固定 TP 或要求占用全部设备。未指定时按[完整设备观察](customer-observations.md#默认设备池)形成候选池；显式列表不能扩大。先核验范围内设备，再结合真实权重、精度上下文、KV 布局、运行开销和版本支持的 TP 选择满足容量的最小组合，按 ID 排序并跳过占用卡。缺完整观察、零设备或容量不足时阻塞，不猜测卡号。观察不是设备锁；部署入口在实际启动前再次核对。

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

`--resume` 绑定原 task.json 字节、run-id 与原回执。已准备文件重新核对摘要，已执行配方冻结；环境继承原选择，安装前后核对实际 Python 与 EvalScope 版本绑定，每次有效正式准备仍调用脚本。迁移和评测原始引用均保留；有效评测通过公开 analyze 只读复核后复用，不重新推理评分。导出重试不部署、不重新评测；发布前仍重新核验完整来源。

退出码：0=计划/安装帮助/安装预览有效或完整交付完成；2=客户字段/顶层引用错误；3=依赖、人工决策、语义复核、AISBench 交付边界或其他未完成；1=执行/证据失败或取消。以 status、steps 和独立回执共同判断，计划 not_run 不表示执行成功。资源状态以原始 verify 回执及本次观察为准，历史 retained 不等于当前存活。
