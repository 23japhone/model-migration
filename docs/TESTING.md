# 验证与证据

以下命令从仓库根执行，控制解释器为 `python3`（要求 3.10+；本次观察为 3.12.12）。`-B` 避免写入源码字节码缓存。每块命令独立执行并记录结果，不能因上一块失败漏掉无依赖检查。不安装缺失工具，不把文档检查扩大为下载、服务启动或 NPU 任务。

## 当前验证基准

### 2026-11-25 单机可变卡数 TP 采集（离线）

运行 `python3 -B -m unittest discover -s tests/vllm-model-migration/worker_capture/parallel_cases -p 'test_*.py' -q`。2、4、8 个本地进程分别验证同一调度的前向编号传递。4、8 个模拟 worker 的完整会话从公开 CLI 封存后，按同一个输出 Token 查回每个 rank 独有的缓存值。八卡用例还用 CPU PyTorch 读取原生格式的张量，覆盖成员绑定被改写、文件串卡、缺前向和封存后文件丢失，逐项核对完好 rank 仍可读取。

该组测试使用乱序、不连续的物理卡编号和合成证据。四卡、八卡模型推理、HCCL 建组和设备端采集仍待实机验证。[离线会话用例](../tests/vllm-model-migration/worker_capture/parallel_cases/test_tp_session.py) · [卡数和设备映射](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#单机张量并行联合采集)

全量离线回归 720 项通过，其中多卡专项 26 项。独立复跑的 52 项 `source_contract` 全部通过。结构审计有 0 个错误、303 条软提示。本次新增一条提示来自 `seal_members` 核对登记和封存时的成员数及逐 rank 身份，复杂度为 12，硬阈值为 15。

### 2026-11-25 Token 首个异常定位

在昇腾 910B2 单卡同步 Eager 上运行 Llama-3-8B-Instruct，使用 EvalScope 1.14.1 和 GSM8K 的 `default/test.jsonl#0`。为检查代码，生成预算设为 3，GPU 成绩与预测包均为人工构造。首轮 NPU 原生准确率为 0，GPU 测试分数为 1，质量报告显示可比、未达标。正式复测仅发出一个推理请求，实际输入 593 个 Token，输出 `[10267, 596, 1464]`，结束原因为 `length`。两轮各评分一次。新阶段选题入口单独从同一份质量报告定界并绑定到该题。

| GPU 测试数据 | 预期与实际 |
| --- | --- |
| 复制 NPU 三个位置的全词表预测 | 三步均匹配，首异常为空，未触发 CPU 前向 |
| 只给第一个输出位置、词表 ID 0 的 raw logit 加 4 | 两端该步均选 10267，首异常为 1；零绝对容差下测得原始分差 4，同权重 CPU FP32 完成该步 logits 和概率复核 |

服务停止回执为成功，端口关闭且本卡无计算进程。随后用实机的四次前向在 CPU 回放改进后的 worker，验证无输出的预填充分块写入零个向量。其余三步的六个向量与实机参考逐文件 SHA256 相同，共 3,847,680 字节；实机初版包含该分块的额外一对向量。Python 3.12 全量离线回归 755 项通过，其中 Token 专项 50 项。独立 source_contract 52 项通过，结构审计 0 个错误、320 条软提示。专项回归及固定的 11 组人工输入见[Token 对齐测试](../tests/vllm-model-migration/token_alignment/)。

### 2026-11-25 任务质量对齐单题验收

实机验收从 `precision.py --action quality` 进入精度阶段，在昇腾 910B2 上使用 Qwen3.5-27B、EvalScope 1.14.1 和 GSM8K 原生任务 v1.1。正式题库只有测试集首题 `test.jsonl#0`，参考答案为 18。四个训练样本用于构造原生提示。

| 环节 | 实测 |
| --- | --- |
| 准备与服务 | 输入准备 2.48 秒。启动与探测命令耗时 478.94 秒，退出码为 1。服务日志记录了一次启动探测请求，随后服务完成了正式评测请求 |
| 单题质量重跑 | 服务端访问日志新增 1 次正式推理，EvalScope 的原生 `accuracy` 为 1/1。单请求耗时 32.82 秒，首次质量流程共 274.60 秒，包含服务与模型资产身份核验 |
| 同基线复用 | 精度阶段重新选择并验证第一次的原生回执，标记 `reused`。耗时 0.38 秒，服务端新增正式推理 0 次，两份报告的分项和结论一致 |
| 采集与原始结论 | 实际启动配置关闭 worker 采集，质量任务直连服务，`collect_perf=false`，未创建 HTTP 正文捕获目录。初次填报的 GPU 分数按参考答案构造，缺少 GPU 原生凭证，相对目标为 `not_evaluated`。验收专用服务已停止 |
| 无客户基线 | 分别用仓库 ID 和裸模型名检索。裸名 `qwen3.5-27B` 在本轮索引命中 4 个同名仓库，读取到 3 份模型卡，另 1 份读取失败。检索用时 9.98 秒，未取得完整 GPU 原生记录，质量对齐单独记为未评估，专用推理为 0 次 |

为验证比较代码，另用两份合成 GPU 归档复用上述 NPU 原生回执，正式推理新增 0 次。在零容差下，GPU 分数分别设为 1 和 0，两次比较的 NPU 分数均为 1。总分和 `default` 分项均达标，逐题结果分别为保持正确、错变对。离线失败用例的两侧分数均为合成数据，三题准确率从 2/3 降至 1/3，零容差结论为 `fail`，其中两题对变错、一题错变对。实际 GPU 与 NPU 的精度对齐仍待同条件 GPU 实测数据验证。

客户总入口 `migrate.py --task --context` 的离线验证用固定回执代替上游部署和普通精度评测。模拟公开基线不可用时，质量项为 `not_evaluated`，客户流程继续生成并校验三个交付 JSON。从客户总入口到三文件交付的完整实机运行仍待验收。

全量离线回归 705 项通过，其中任务质量专项 106 项，另独立复跑了全量中的 52 项 `source_contract`。多子集、连续分值、阈值相等、公开基线来源歧义、历史题库和已发布容差均有离线回归。结构审计为 0 个错误、302 条软提示。[任务质量回归用例](../tests/vllm-model-migration/task_quality/) · [软提示处理依据](CODE_QUALITY.md#机器策略基线与当前审查决定)

### 2026-11-25 单卡 Eager 的 MLA 缓存

全量离线回归 599 项，独立 `source_contract` 52 项通过。`worker_capture/cache_cases/test_mla*.py` 覆盖后端识别、跨页写槽、历史加载、分量错绑、预算不足和已封存文件损坏。公开查询验证同一次前向仍能读取完好分量。字段与模式范围见[MLA 采集契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#单卡同步-eager-的-mla-缓存)。

### 2026-11-24 单卡 ACLGraph

全量离线回归 592 项、独立 `source_contract` 52 项通过。图模式用例位于 `worker_capture/graph_cases`。单卡实机覆盖 Llama 整图、Qwen 分段图，以及两种模型的整段 prefill、分块 prefill 和连续 decode。正式请求的 Token 对照、重放次数和缓存原值核验见[单卡 ACLGraph 实测记录](MSPROBE_RUNTIME_CAPTURE.md#单卡-aclgraph-纯文本生成)。

### 2026-11-24 双卡图文、SSE 与固定 seed 采样

完整离线回归 539 项、独立 source_contract 52 项通过。新增 13 项覆盖图像占位及融合掩码、跨块图像来源、双 rank 多轴位置一致性、流式事件与输出序号对应、客户端断流、随机采样元数据及实际非最大概率候选、图像摘要与失败写盘的预算记账。测试位于 `worker_capture/generation_cases`，随本页的 unittest discovery 执行。两种采集工具的分工和公开查询参数见[双卡生成采集契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#双卡图文流式与固定-seed-采样)。

实机验收封存了 Llama 的两组开关对照和 Qwen 红图的关闭观测请求。各组输出、按层回查结果及收口时的覆盖范围见[双卡生成实测记录](MSPROBE_RUNTIME_CAPTURE.md#双卡图文流式和固定-seed-生成)。

### 单机双卡 TP=2

本次实现已运行本页全量离线回归（526 项）和独立 source_contract 专项（52 项），均通过。结构审计为 0 个错误、222 个软提示。

双卡专项覆盖跨进程实际传递同一前向标识、重复 Token、无输出分块、非输出 rank 的实际张量、错绑目录、缺 rank、错位输入、损坏快照、整数预算分配和观测失败隔离。使用本页全量 unittest discovery 和独立 source_contract 入口。单机双卡的设备证据及采集范围见[运行时实测记录](MSPROBE_RUNTIME_CAPTURE.md#单机双卡张量并行)。

### 2026-11-23 架构识别和联合采集

全量离线回归包含 505 项。新增回归覆盖配置和加载实例层数核对、64 层混合栈、另一种模块路径、共享实例或歧义、同前向按层查询、预算不足和快照摘要错误。目标镜像中另外使用真实 msProbe 和 mindie-debugger，在小宽度随机权重的 Transformers Qwen3.5、Llama 和 GPT-2 上核对逐层输入输出、缓存状态及关闭/开启观测的 logits。CPU 用例分别执行 64、4、3 层，设备算子的槽位验证另按 NPU 实机结果记录。

真实 NPU 对照使用 Qwen3.5-27B 和 Llama-3-8B-Instruct 原始权重、单 rank、BF16、同步 Eager、Model Runner V1。对同一模型的两个请求都开启 msProbe，并使用相同的 Chat 输入和 greedy 参数。两模型在开启与关闭缓存观测时各自输出的 Token ID 都为 `[19, 11]`，其第二个输出的联合采集分别覆盖 64 和 32 个文本层。数据范围见[采集记录](MSPROBE_RUNTIME_CAPTURE.md#按实际层结构联合采集)。

### 2026-11-23 冷前缀完整请求

用 `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q` 运行 515 项完整离线回归，另行执行的 52 项 source_contract 通过。新增会话用例覆盖无输出分块、长度为一的最后一块、首个输出、连续 decode、重复 Token ID、提前 EOS、查询选择器和原生数据摘要变化。CPU 张量用例核对跨页 K/V、写错槽、GDN 清零或续块、物理状态更新、单前向和请求总预算。实机验收应同时记录实际调度、逐前向层覆盖、公开入口能读回的原值、msProbe 与缓存分别落盘的字节及相同输入的输出序列。[查询与预算契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#从完整请求回查每次前向)。

### 2026-11-21 V1 decode 缓存采集

使用 Python 3.12 和 CPU PyTorch，按本页的 unittest discovery 运行全量 498 项、worker_capture 64 项、source_contract 52 项，全部通过。公开 `capture-session` 和 `capture-read` 覆盖单次 HTTP、指定输出序号、两层共六份写后或前后数值、计划错位和部分失败封存。使用上游 Python 方法原文和真实 MemoryWatchpoint 的复核范围见[采集记录](MSPROBE_RUNTIME_CAPTURE.md#v1-单次-decode-的缓存采集)。本轮未运行目标 NPU。

结构审计 0 errors、196 warnings，开发基线为 194 warnings。新增两处软提示分别来自缓存观测摘要交叉校验和原始数值文件解码，两者复杂度均为 11。生产数据读取仍校验普通文件、SHA256、长度和数值类型。已用原生成器登记资源，策略和硬阈值保持原值。

### 2026-11-17 msProbe 实机闭环修复

修复启动导入遮蔽、新会话目录写入顺序和目标 chat serving 适配，并补齐编译装饰器直接 Eager 调用的模块采集及原生 None/空容器返回值解析。采集专项 52 项、全量 486 项、source_contract 52 项通过；结构审计 0 errors、194 warnings。RED/GREEN、范围与实机边界见[修复记录](MSPROBE_RUNTIME_CAPTURE.md#2026-11-17-实机闭环修复)。

### 2026-11-17 msProbe 运行时采集拆分

独立专项 42 项通过，含 9 项不读取 GPU 基线、不导入评测 SDK 的公开会话闭环。测试范围、替身与实机边界见[采集交付记录](MSPROBE_RUNTIME_CAPTURE.md#2026-11-17-离线验收)。源码及契约仅扩展 verify-agent，评测流程保留原版。全量回归及结构审计仍按下列命令执行；本机离线结果不能替代真实 NPU 验收。

### 2026-11-05 客户缺省镜像

在 `a308630` 的独立代码副本上，原有 390 项测试通过。新实现的全量离线回归通过 434 项，其中 `source_contract` 专项通过 52 项。用以下命令复验：

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration/source_contract -p 'test_*.py' -q
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q
```

该版本的 12 个业务 Skill 共 339 个受版本管理的文件。静态检查覆盖 253 个 Python 文件、42 个 JSON 文件和 2 个 shell 脚本，20 个公开 CLI 均通过 `--help` 检查。

客户流程集成用例经过实际公开镜像发现、准备阶段及镜像任务 CLI，Quay 和 Docker 使用离线替身。离线验证覆盖跨页选版、正式／修订／RC 数字排序、Agent 引用或 image ID 冲突时在拉取前阻塞、按平台 digest 拉取，以及按多架构索引预拉后零次拉取复用。另用原代码生成整体阻塞、镜像已完成的准备回执，升级后沿用该镜像并复核本地缓存。实际目标主机的拉取、部署和 NPU 验收仍待执行。

Quay 分页与排序归准备阶段，客户默认值模块绑定选中身份与历史回执，镜像任务核验 Docker 内容。改动前结构审计有一项既有基线须收紧（精度结果模块 89→87 行）。按仓库命令记录新文件和实际指标后，普通审计为 0 项错误、177 项软提示；策略和硬阈值不变。阶段镜像来源复核保留在原职责内，其复杂度由 15 降至 12。

### 2026-11-03 共享 NPU 与此前验证

下表保留 2026-11-03 共享 NPU 功能提交 `1ec7ff8` 合入本地 `dev`（`e182114`）时的验证结果。当时已包含空闲优先共享部署、容量观察、镜像预检、设备映射和数据准备修复。命令与审查见[共享实现记录](SHARED_NPU_IMPLEMENTATION.md#验证与审查记录)。

表中实机记录对应当时选定的模型和环境。真实共享启动、服务当前存活状态和完整客户交付均须单独验收。

| 检查 | 2026-11-03 验证结果 | 证明边界 |
| --- | --- | --- |
| 标准库回归 | 390 项通过，无跳过；含新增共享专项27项，设备专项合计78项 | 包含空闲优先/混合/四卡全占用、5%预留、逐卡短板、启动前资源变化、运行归属、用户决策及三文件交接；设备和服务边界使用替身，不证明真实共享部署 |
| 静态语法 | 249 Python、42 JSON、2 shell 通过 | 不导入原生评测 SDK、不加载模型 |
| 公开 CLI | 20 个 help；12 个主入口计划、7 个辅助计划及客户计划在回归中通过 | 不同 cwd、中文/空格搬迁副本、清除 PYTHONPATH；计划不证明业务就绪 |
| 业务静态资源 | 12 个 Skill、335 个文件，搬迁与注册复制回归核对路径、字节与权限 | 完整副本可复核，不证明宿主发现或目标运行 |
| 开发技能与文档 | 12 个开发技能、37 项资源依赖闭包及 75 份文档检查通过 | 忽略的个人规则不计入共享文档；不证明新宿主实际加载 |
| 结构审计 | 0 errors、180 项软提示；299 文件、46 目录、1949 个 Python 函数 | 只按新增文件和实际改善更新基线；未提高已有指标或阈值，新增软提示的保留理由见[共享实现审查](SHARED_NPU_IMPLEMENTATION.md#审查发现与处理) |
| 共享 NPU 实机验收 | pending | 真实四卡共享启动、驱动/容器兼容、功能和精度尚未执行；两次容量观察与5%预留不构成设备锁 |
| 设备映射实机验收 | pending | 下述续跑曾取得设备探针通过，但未形成完整有效部署交接；2、3号卡直接部署按用户要求省略探针，不作为映射链验收 |
| 不变边界 | 原版 install_evalscope.py、客户模板和三个交付 Schema 未改；git diff --check 通过 | 安装脚本的字节摘要见[安装契约](../skills/vllm-model-migration/references/evalscope-installation.md#原版脚本与来源) |
| 既有 GSM8K 本地加载 | datasets 4.8.4 下21组通过 | 真实公开准备 CLI、客户子集/limit归一化、评测配置及 sample_ids，真实 datasets 加载并比较样本；完整 EvalScope 适配器发现、原生 limit 消费和模型评分未运行 |
| 镜像、catalog 与预算入口 | 已有原服务器验证，范围见下文 | 复用既有环境；镜像只使用自有探测容器；45条样本长度及预算对照通过；未启动模型或评分 |
| 既有数据准备、预检与冻结 | 两个 C-Eval 科目、GSM8K 各 15 条原生加载通过；冲突与篡改反例均拒绝 | 此前真实 EvalScope 1.14.1；合成部署回执、不存在的模型端点；不证明本轮默认目录查找或联网下载已实测 |
| 退出容器替换与部署功能 | 原服务器新建及复用服务的文本/Chat/SSE通过 | 仅覆盖所述固定镜像和Qwen3.5-27B；不推导当前存活或其他组合通过 |
| 原生精度与完整实机验收 | 历史一项 C-Eval 子集有效；本次续跑仍 blocked | 本次三个子集尚未评分、三个JSON未导出；下述独立性能基线不替代完整交付 |
| 2、3号卡直接部署与性能基线 | 三类功能请求通过；一次16请求压测成功 | 由 Agent 直接启动并调用 EvalScope，未走完整 Skill 部署交接；候选服务已退出，优化效果未评估，见下节 |

近期修复的 RED-GREEN、定向测试和服务器证据分述如下；更早的职责与结构背景见[实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md)，旧版本计数不作为当前基准。共享依赖只绑定 Python/venv、CLI 和 EvalScope 版本，未冻结全部发行包；两端实际进程证据齐备后才能验收共用环境。

本仓尚无 CI、Ruff 复杂度规则、类型检查或自动依赖边界检查器。结构审计与当前回归各有明确覆盖范围，不代表完整静态分析或跨平台验收。

### 2026-11-03 Qwen3.5-27B 断点续跑与直接部署

本节记录同一会话的服务器运行，与上表离线代码验证分别判断。2026-11-03 20:26（Asia/Shanghai）只读收尾核对确认：客户流程仍为 `blocked`，当前候选容器已退出，服务端口不可达，三个交付 JSON 尚未发布。收尾仅读取状态、日志及既有产物并保存观察副本，没有新增推理、设备探针、压测或停服。

**任务与版本。** 沿用 `qwen35-27b-json-20261103b`，原任务 SHA256 为 `3d7229d889e2b3c1c9eb8b89716707982955beb7a0d8bbd38c479c4be26ed946`。任务为 Qwen/Qwen3.5-27B，允许池0–3，C-Eval accountant、advanced_mathematics和GSM8K default各最多15题；原任务与历史失败回执未覆盖。镜像继续复用 `quay.io/ascend/vllm-ascend:v0.20.2rc1`，实际 ID `sha256:27d1146419e99981c0a676a3fff102d2119fa6ecf5ae00e5c83aab611f5b7733`；容器 vLLM 模块版本为0.20.2，发行包元数据为0.20.2+empty，vLLM-Ascend为0.20.2rc1，宿主 EvalScope为1.14.1。

收尾时的服务器资源与起点 `68c147a` 的本地资源进行了完整文件集合及 SHA256 比较：服务器329文件、本地331文件；服务器缺少 `verification/customer/npu_capacity.py` 和 `verification/devices/capacity_table.py`，另有10文件内容不同，涉及客户编译/执行/冻结、容量观察入口、verify输入校验及文档。因此该次续跑不证明当前 dev 的最新容量观察、提前预检及版本匹配代码均已实机通过；不得用当前仓库提交替代实际服务器资源身份。

**Skill 续接与直接执行的边界。** 按授权精确停止四个仅有保活进程的占卡容器后，一次设备探针通过但注册目标版本不符；新建 `registration-check-02.json` 和 `agent-context-08.json` 将目标公共版本与迁移目标统一为0.20.2。随后尝试再次在设备阶段报 `aclInit failed: 500000`，注册步骤未执行，不能将“请求已修正”写成“注册复验通过”。1号卡上的另一个容器在停止前复核时已启动真实服务，故未停止。内核曾记录1号卡 `Conflict open udevid`；仅无计算进程或显存可用不能证明可被另一个容器使用，采样也不构成设备预约。

用户随后明确要求直接使用2、3号卡且不再执行探针。Agent 保留原 Skill blocked 回执，以原启动配方直接创建容器，未生成该载体的完整设备映射/有效环境交接，也未执行 verify review → execute finalize。直接功能及压测证据保持独立，不伪造 Skill completed 或导出 ready；当前 CLI 没有无需映射证据即可完成三文件交付的路径。

**已完成的基线。** TP=2、BF16、max_model_len=8558、gpu_memory_utilization=0.9、max_num_seqs=8、max_num_batched_tokens=8192，eager模式、禁用异步调度和前缀缓存，保留真实 argv 中的 profiler_config。基线容器 `c4619edd15c8…` 的text、chat、完整SSE均返回HTTP 200、答案42、输入26/输出3 token；首次text耗时91.3884秒，后两项约0.87秒，不能将首次耗时与预热后稳态混用。随后该自有容器已为候选测试停止并删除，日志及启动证据保留。

性能启动前公开 `check_evalscope_handoff.py` 返回ready，实际使用其绑定的宿主 EvalScope CLI/Python和环境。首轮perf因URL少`/v1`返回404，保留失败；修正为完整实际端点后，random数据、目标输入512 token、固定输出128 token、并发4、16正式请求、2预热、seed=42、流式完成一次基线。原生参数 `extra_args={}`，没有显式关闭thinking，不能把功能请求的enable_thinking=false归到压测条件。

| 基线指标 | 原生结果 |
| --- | ---: |
| 成功 / 失败请求 | 16 / 0 |
| 实际平均输入 / 输出 token | 512.1875 / 128 |
| 计时区间 | 175.2531 s |
| 输出吞吐 | 11.686 token/s |
| 平均请求延迟 | 33.9187 s |
| 平均 TTFT / TPOT | 2086.68 / 250.65 ms |

这是一组固定负载的单次测量；没有重复试验、GPU对比、性能目标或有效候选成绩，不得声称最优配置、提升比例或达标。

**候选与未完成项。** 候选仅移除 `--no-async-scheduling`，日志确认异步调度启用；容器 `090b9ee88f5d…` 在19:43:35报告 `VllmWorker-0 died unexpectedly`，继而 `Executor failed` / `EngineDeadError`，19:43:39退出。Docker记录ExitCode=0、OOMKilled=false，但服务失败且端口不可达；退出0不代表运行成功，日志不足以归因于异步调度或外部停止。候选无功能/性能结果，当前没有可用的本任务服务。三个子集仅完成准备/catalog并生成直接评测配置，尚无本次评分；历史其他运行的C-Eval分数不能移作本次结果。

**证据与续接位置。** 位于原服务器任务目录 `codex-vllm-qwen35-27b-opt-20261103`，以下路径相对其中的 `evidence/qwen35-27b-json-20261103b/`；task、context及新注册请求位于同级任务 `inputs/`。所有路径均是证据定位信息，不是自动执行指令。

| 证据 | 相对位置 |
| --- | --- |
| 最后客户回执 | `customer-workflow/attempts/cfd9c3f2e7894a81aba7a8c9f841112e/customer-manifest.json` |
| 直接启动及功能响应 | `direct-deploy-23.json`、`direct-functional-23.json` |
| 性能环境交接 | `evalscope-handoff/e5d56069640e4e5a91b040df53dd8830/handoff-check.json` |
| 成功基线、实际参数、逐请求数据库 | `performance/baseline-01/`，原生输出在其 `outputs/Qwen3.5-27B/parallel_4_number_16/` |
| 候选启动及收尾观察 | `direct-deploy-23-async.json`、`knowledge-closeout-20261103/runtime-observation.json`、同目录`candidate-service.log` |

收尾观察SHA256为 `3e066379003af8a39edd29220bacb5123d9be6ec845bfb54174763c6471be736`，候选日志SHA256为 `fb4d06785ed435825d4d566beac62e98b2f678a79d1177a947dd6df9da8eb109`。续接须先核对实时资源、保留不再探针的用户约束，并解决直接部署证据与现行三文件契约之间的缺口；不能将手写候选放入deliverables冒充正式发布，也不能静默重启整个客户任务。共用环境目前只证明本次性能侧采用绑定环境，精度侧尚未执行，完整两端共用验收仍pending。

### 2026-11-03 镜像来源转换与提前计划校验

主题提交 `8741940` 与最新容量观察 dev `ad25cdf` 集成后，363项回归通过，无跳过；业务代码自动合并，验证文档保留双方记录，容量流程测试新增预检先于观察的顺序断言。联合版本静态语法245 Python/42 JSON/2 shell、结构审计0 errors/174既有软提示，未运行真实部署。以下保留主题提交单独验证的证据。

方案：镜像观察分离为预检 sources 与容器 runtime_paths，原观察不修改；在准备模型可用后、功能输入观察/分配/容器替换前调用公开 precheck.py --plan。计划校验失败仅阻塞部署及依赖步骤，独立准备结果保留；不伪造模型配置把完整校验提前到准备之前，不将 not_run 当作静态支持通过。运行文件清单仍由既有 runtime.files 声明并在载体核验，runtime_paths 仅作来源映射证据。

职责与变更形状如下；没有新增生产模块、私有跨 Skill 导入或第三方依赖。

| 文件 | 所有者与职责 | 消费者/边界与依赖 | 增长与放置理由 |
| --- | --- | --- | --- |
| customer_workflow/compile/deployment.py | 客户编译器；来源字段投影及容器路径验证 | 私有编译入口；标准库及已有公开合同 | 126→158行，与已有预检生成同属一种职责 |
| customer_workflow/effects.py | 客户公开操作委托；提前调用预检计划并保存终态 | runner；仅通过固定公开CLI调用子Skill | 82→113行，未建立新的效果所有者 |
| customer_workflow/runner.py | 客户流程编排；调整检查顺序 | 已有编译及效果边界 | 253行不变，migrate复杂度7/分支5/语句30保持不变 |
| tests/vllm-model-migration/source_contract/ | 镜像观察到预检接口联通回归 | 合成Docker边界、实际编译器与公开计划CLI | 独立能力测试目录，不继续增长根级平铺测试文件 |

修改前结构审计0 errors/174 warnings。先用真实 image.inspect 输出结构（仅模拟Docker效果）经编译器调用公开 --plan，复现未知字段退出2；非法字段/路径和提前阻断用例同样先失败。修复后8项专项及349项全量回归通过，无跳过。另补续跑回归先复现成功计划残留旧失败诊断，再修复；取消和异常计划输出均保存非running终态。两项既有测试只隔离新增计划边界，原容器替换及主机身份断言保留。

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration/source_contract -p 'test_*.py' -v
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q
```

静态语法243 Python/42 JSON/2 shell通过；结构审计0 errors/174既有软提示，无新增提示、阈值或基线修改。新增source_projection复杂度7/分支5/语句17，runtime_source_path复杂度7/分支3/语句8，precheck_plan复杂度6/分支5/语句23；既有软提示所在函数未增长。未运行真实Docker、模型、NPU、完整精度或三JSON交付；公开计划只验证请求，不证明来源内容或部署可用。

### 2026-11-03 新部署消费最新容量观察

本修复主题提交基于 `5e280cc`；本地 dev 集成时保留已合入的 NPU 映射空白及版本比较修复，联合验证结果见上方当前基准。实施顺序为：先增加失败测试 → 新增确定性解析/证据绑定 → 改编排输入 → 保留创建前复核 → 验证公开入口及全量回归。旧 context 的 devices 不再参与新部署分配；首次及失败续跑必须本次观察，已成功部署不重复启动。

Change Shape Map（路径相对各所有者）：

| 文件/所有者 | 职责及消费者 | 边界/依赖 | 增长与放置 |
| --- | --- | --- | --- |
| verify `devices/capacity_table.py` | 910B HBM/进程表确定性解析，供容量观察消费 | 私有纯函数；标准库和共享 require | 新增独立解析职责，约75行，无运行效果 |
| verify `customer/npu_capacity.py` | 本次观察、证据绑定及容量委托，供公开 customer CLI 消费 | 私有；同任务设备解析、日志/证据与容量规划器 | 新增约55行，与主机效果同属 verify |
| verify `customer/inspection.py`、`cli.py` | 暴露可选容量模式及观察绑定 | 既有公开 CLI 加可选字段；原离线 allocate 兼容 | 小幅组装；不新增入口 |
| 总入口 `runner.py`、`compile/deployment.py`、`recovery.py` | 先观察后分配；动态状态不冻结 | 仅公开 CLI 和带摘要回执；无兄弟私有导入 | 既有职责内修改 |
| `tests/vllm-model-migration/devices/test_capacity.py` | 解析、公开操作、续跑与失败边界 | 模拟命令/主机；执行实际编译器和规划器 | 新增设备专项测试；不增加测试根平铺文件 |

失败边界：observe 产生完整设备、主机/时间/原文引用，失败仅阻塞分配、容器处理及依赖部署的后续项；allocate 消费本次回执，主机漂移、过期、证据变化或资源不足均 blocked，不清理容器或重试。创建前 existing available 检查仍独立执行。独立资产/数据准备保留，最终完整交付不会因局部成功报告 completed。

修改前 `runner.py::migrate` 复杂度7/分支5/语句30，修改后相同；`inspection.py::host` 从3/2/9到4/4/13；`recovery.py::freeze` 从3/1/5到3/1/6，参数数均未变化。新增解析函数最大复杂度9，不新增硬热点；原有174项软阈值告警未增加。新增文件按既有要求更新结构基线，不提高硬阈值。

RED：Python 3.12 下原实现先出现 allocation，缺少 observation 引用，动态 devices 仍被冻结；新增解析/观察接口缺失。初次误用本机 Python 3.9 的注解报错仅为解释器前提错误，不计入缺陷复现。GREEN：新增14项专项测试及独立基线全量348项均通过，无跳过；`python3.12 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q`；设备新测试覆盖旧空闲/新占用、显存下降、缺卡/未知表/非法单位、观察期间主机变化、原文/回执摘要变化、60秒过期及未来时间、公开 observe→allocate、失败续跑、旧冻结记录兼容、静态配方变化拒绝、容器创建前占用拦截。旧容器替换测试同步补齐公开观察会产生的回执。

另回放上次保存的实机 `npu-smi 25.5.1 info` 日志（SHA256 `471cdf9af234c2b7880a102f0d084ce1bd931fe53e886fabc1740c50863eadb9`），正确解析8张卡，原允许池0–3全部 occupied=true，原容量配方在分配阶段拒绝，不再选出1、2号卡。仅只读取得既有日志并本地回放，未部署新代码到服务器、未采集当前卡状态、未启动模型或进行精度/性能测试。采样与启动不是原子操作，本修复不提供设备预约或跨任务锁。

### 2026-11-03 vLLM local 版本匹配修复

部署前检查原先只移除运行版本的 `+` 后缀，目标与运行均为 `0.20.2+empty` 时误判不一致。方案沿用迁移阶段既有规则：目标不含 `+` 时比较公开版本，含 `+` 时精确比较完整字符串；不改写安装包版本、镜像身份、准备配方或冻结记录。生产变更仅在 verify-agent 的 `verification/inputs.py::precheck_identity`，无新模块、共享依赖或 Schema；用现有客户运行环境测试约束两阶段行为，避免引入跨 Skill 私有依赖。

新增4项回归：14组版本关系分别经过迁移及部署检查，拒绝不同公开版本/local构建以及 rc/dev/post 与正式版本混用；另覆盖缺失/空/非字符串版本、最终载体完整版本及文件内容漂移、镜像探针保留 `0.20.2+empty`。每次比较保留原始请求及回执。旧代码上相同 `+empty` 和 `+vendor.1` 均误拒绝，同时复现空版本误通过、null版本抛出非预期异常；修复后定向25项、全量341项通过，无跳过。

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_customer_runtime.py' -v
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q
```

`inputs.py` 126→131行，函数复杂度5→9、分支4→7、语句12→16、参数2不变；测试文件386→474行，均未触发新软阈值。结构审计0 errors、174项既有软提示，未调整基线；现有 `receipt_fields` 的复杂度提示对应未修改函数。实际载体执行仅在外部执行边界模拟，镜像探针读取临时安装包元数据；本轮没有操作服务器镜像或容器、启动服务及重跑真实NPU验收，不代表部署或三个JSON交付已经完成。

### 2026-11-03 npu-smi 字段尾部空白修复

在 `5e280cc` 基线上，先增加真实 `npu-smi 25.5.1 info -m` 输出回放及空白变体回归，复现 MCU 名称尾部空格触发“首版只支持 Ascend 910B 整卡映射”：真实样本和6组空格/制表符、LF/CRLF组合均失败。随后仅在 verify-agent 的 `devices/inventory.py::mapping` 字段拆分后执行 `name.strip()`，MCU跳过及计算芯片校验消费同一规范化名称。

真实样本以转义字符串保存在 `devices/test_probe.py`，与服务器原始日志逐字节一致（1981字节，SHA256 `16edde1882d422ac2b89e9f5973c857adf33bc19a58a9007875348f2efe00bff`）。新增3项测试覆盖全部8张卡及进程映射、6组空白变体，并确认未知硬件、非法计算Chip/逻辑编号、重复映射和非MCU名称仍被拒绝。修复后设备专项37项、全量337项标准库回归通过，无跳过。

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration/devices -p 'test_*.py' -v
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q
```

生产模块仍由 verify-agent 设备观察负责，无新增接口或依赖。`inventory.py` 46→47行，`mapping`复杂度8、分支3、参数1不变，语句16→17；测试文件109→159行。结构审计0 errors、174项既有软提示，无新增软提示或基线调整。该证据证明真实文本可解析及既有离线行为通过；本轮未启动模型、操作容器或重跑完整设备/精度验收，不能据此宣称服务器设备空闲或三个JSON已完成。

### 2026-11-03 GSM8K 本地子集布局兼容

新增检查在旧实现上复现派生 README 缺失、元数据无摘要保护，以及真实 datasets 加载 main 时的 `BuilderConfig 'main' not found. Available: ['default']`。修复后布局专项10项、全量334项标准库回归通过。默认目录专项原有目录清单断言同步包含派生 README，来源和客户路径绑定断言保留。

显式 SDK 检查使用5题合成 test 数据、独立 train/socratic 文件，在本机已有 datasets 4.8.4 上运行21组：三种 subset_list 写法分别组合 limit 缺失/null/-1/1/3/5/99。公开 prepare 生成目录，实际客户编译器生成选择、配置和 sample_ids；加载内容与原 test 文件逐条一致，每组子集写法的所选样本摘要一致，源目录字节保持不变。题数依次为5/5/5/1/3/5/5。

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_dataset_layout.py' -v
python3 -B tests/vllm-model-migration/evaluation/gsm8k_native_check.py --backend datasets
```

datasets 模式实际运行底层 loader，核对准备选择、最终 limit 参数和相同前缀；它不冒充原生评测器已经应用 limit。若已有 EvalScope 环境，可独立运行下列更完整检查，使用实际 adapter 发现、load_dataset 和本 Skill 的逐样本 verify；不安装缺失依赖、不下载、不推理。本轮本机没有 EvalScope 包，此模式未运行，不能写成全部原生预检已通过。

```bash
python3 -B tests/vllm-model-migration/evaluation/gsm8k_native_check.py --backend evalscope
```

### 2026-11-03 官方默认数据目录优先复用

先以新增回归复现 default_local 字段和 catalog 缺路径指引缺失，再实现来源选择；审查回归另复现默认位置为单个文件时误扫父目录其他文件，修正为保持原候选文件入口。最终19项专项及331项全量回归通过，无跳过。专项命令：`python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_dataset_source_resolution.py' -v`；全量命令见下文路径兼容回归。

真实临时文件覆盖 C-Eval/GSM8K 默认目录读取、原数据不变、独立 EvalScope 布局和客户配置路径绑定；默认目录缺失时用合成 HTTPS 响应执行实际落盘、解压、内容核验，确认写入原请求路径。还覆盖已有请求路径优先、单文件候选、空目录/缺 split/坏格式/歧义/权限/链接错误、官方资料及数据在准备期间变化、下载失败与取消证据保留、输出边界、下载包摘要不可被本地复用绕过、plan 无读取/下载效果，以及 metadata_only 的缺路径提示。原生 catalog SDK 边界使用替身；未真实联网下载、安装依赖、调用原生评测 SDK、访问模型或操作设备。

### 2026-11-03 设备编号映射

客户选卡保持物理编号，最终载体通过 CANN 双向查询及进程索引查询生成、复验逻辑号。全设备 worker 观察、只读 inspect-devices、精度前后复核和有效环境导出贯通；原始请求保持不变。34项专项及312项全量回归通过，具体 RED-GREEN、结构审查与实机缺口见[实现记录](DEVICE_MAPPING_IMPLEMENTATION.md)。新建与 borrowed Docker、原生进程、接口缺失、越界/占用、PID/容器/宿主身份变化和证据篡改由模拟边界覆盖；没有在本轮执行实际服务器操作。

### 2026-11-03 数据准备与评测交接

新增20项回归，先复现目录整理、缺省路径绑定、预检入口、正式catalog和失败隔离缺失，再实现。最终全量278项通过，无跳过；静态语法、20个help、开发资源、文档和结构检查通过。取消回归复现预检中断后仍留下running回执，修复后KeyboardInterrupt/SystemExit均保存failed且所有精度结论保持not_evaluated。

在既有EvalScope 1.14.1、datasets 4.8.4环境使用独立包外副本，构造仅含原始CSV且无README的C-Eval来源，以及包含socratic文件的GSM8K来源。公开prepare自动生成独立布局，公开validator的preflight、绑定准备回执的catalog及正式probe冻结均通过。C-Eval CSV列显式保留字符串类型，并使用所选split的真实表头，防止SDK推断将id改为整数或为val补入不存在的explanation字段；真实原生核验曾暴露该差异，修复后没有放宽样本身份检查。

accountant、advanced_mathematics、GSM8K/default各选前15条；最大输入token分别为219、366、139。逐条原生样本内容、顺序、prompt与答案映射经原文件转换核对；preflight、正式catalog和冻结结果的样本身份相同。原配置省略local_path且字节不变，冻结配置明确绑定asset.path。另验证路径冲突、额外文件、README修改、CSV修改和旧回执摘要均在推理前拒绝；原始数据前后摘要不变。

部署回执为明确标记的测试夹具，端点不可用；没有访问模型服务、加载权重、安装依赖、操作NPU或评分。原生加载成功不证明真实精度结果或完整客户流程完成。客户入口的自动衔接与失败隔离由离线回归覆盖，既有三份交付JSON结构保持不变。

### 2026-11-01 镜像与原生目录兼容修复

本节记录从 `11145b209a85b1057548956336d45a97d8a052be` 开始、在 `codex/qwen35-e2e-probe-fixes` 开发期间取得的证据；提交及合入状态以 Git 为准。旧实现已由回归复现 editable 源码识别失败及 `DatasetDict` 的 `KeyError: 0`；修复后标准库回归 228 项通过，无跳过，静态语法 226 Python/42 JSON/2 shell、20 个 help 和 73 份文档检查通过。全套回归包含公开计划与中文/空格路径搬迁。结构审计 0 errors、166 项既有 warnings；基线登记新增测试并收紧 catalog 主函数指标，未调整阈值。

嵌入的镜像 `PROBE` 字符串不计入常规函数扫描，另行对其 AST 使用同一度量：`module_origin`、`editable_root`、`package_root` 复杂度分别为 9、13、8，均低于硬阈值 15。`editable_root` 超过软阈值 10，保留理由是其仅承担同一份标准来源元数据的解析与验证，17 条语句、6 个分支，无跨边界效果；本轮不为拆指标增加通用解析框架。catalog 主函数复杂度由 7 降为 5；生产逻辑仍只位于两个原模块。

原服务器使用逐项核对摘要与权限的完整 312 文件副本、新尝试目录及既有 EvalScope 1.14.1 环境：固定镜像 `sha256:27d1146419e99981c0a676a3fff102d2119fa6ecf5ae00e5c83aab611f5b7733` 的 image 公开入口返回 completed，识别 vLLM `0.20.2+empty` 与 vLLM-Ascend `0.20.2rc1`，源码快照摘要经预检消费者复核；自有探测容器已按精确身份删除，既有客户容器及旧失败回执保持不变。C-Eval 的 accountant、advanced_mathematics 及 GSM8K 的 default 均盘点 15 条，两个 catalog 入口完成，客户编译器接受其模型/选择/版本/顺序/数据摘要合同。

首轮仅解除两个兼容报错时，三个子集长度仍错误地记录为 2；当时只登记独立问题，未继续部署。其原因和当前行为见[架构中的长度核验](ARCHITECTURE.md#原生目录输入长度核验)，后续获准修复的证据见下节；旧结果不改写为通过。

### 2026-11-01 Tokenizer 长度与预算修复

开发期间在同一工作区继续最小修复，只新增 `catalog.tokens()` 的显式返回参数和非空整数列表校验。新增5项回归先在旧实现复现 3/17/5/9001 个 token 被数成 2、异常返回未拒绝等失败；修复后定向8项、全套233项均通过，无跳过。覆盖默认列表/默认字典、真实2个token、单段/多轮消息、thinking参数、非法输出、无截断、catalog最大值以及足额/不足额预算边界。静态语法、20个CLI help和搬迁回归通过；结构0 errors/166既有warnings，阈值未改。tokens复杂度4→8、分支4→5、语句6→9、参数仍3，低于软阈值，原模块责任未扩展。

服务器使用新完整资源副本与新尝试目录，未安装或改变依赖，EvalScope1.14.1、Transformers5.22.1、tokenizers0.23.3。公开catalog均completed；对每条所选样本显式请求字典输出，读取 `input_ids`，与显式列表输出及实际 `catalog.tokens()` 对照，45条内容与长度全部一致。首条accountant样本仍为142，三组最大值如下：

| 数据集/子集 | 样本数 | 最大输入 token |
| --- | --- | --- |
| C-Eval / accountant | 15 | 219 |
| C-Eval / advanced_mathematics | 15 | 366 |
| GSM8K / default | 15 | 139 |

两组新观察通过 `native_catalog()` 的模型、选择、版本、顺序及数据摘要核验；`context_tokens()` 返回366+8192=8558，与逐样本独立统计一致。以最大输入项调用实际 `evaluation_request()`，将容量设为8557时正确在编译精度请求前阻塞；离线回归另验证足额时继续委托请求生成。该边界检查使用合成容量值，不代表已启动8558上下文的服务或验证真实NPU容量。tokenizer文件和原始数据前后摘要一致，旧错误观察保留；未加载模型、执行评分、调优或导出三JSON。

### 2026-11-02 已退出同名容器自动替换

本节记录从本地 dev `11145b209a85b1057548956336d45a97d8a052be` 建立的 `codex/exited-container-replacement` 开发证据，包含上两节修复；提交及合入状态以 Git 为准。新增13项回归先复现旧实现缺少 consent 时阻塞，再验证自动退出路径、非退出状态保留授权、精确身份/名称/状态变化拒绝、删除失败不强删及成功后继续部署。定向31项、全套246项通过，无跳过；完整回归包括公开计划和中文/空格路径搬迁。

公开 customer.py 的实际子进程在临时 Docker 替身边界执行：确认 plan 无 Docker 效果、无 consent 删除已退出的完整 CID、回执 completed，再由实际 Docker 生命周期模块创建并启动同名新容器；同时验证删除前重启或原 CID 被另一同名容器替代时 blocked 且不强删。编排测试另核对替换失败时不进入部署。这些是离线契约与控制流证据，不是实际 Docker daemon、目标服务器或模型部署验收；本轮未操作真实客户容器、NPU 或模型。

静态语法226 Python/42 JSON/2 shell、20个CLI help、开发技能资源及73份文档检查、git diff --check 全部通过。结构审计0 errors/166项既有warnings；未改策略或阈值，更新测试指标并收紧 replace 的复杂度9→8、分支6→5、语句19→16。审查未发现待修问题；inspect 与 rm 之间没有原子锁，自动路径的非强制删除保护当前正在运行的容器，不承诺消除所有并发变化。完整部署、精度评分、性能调优及三个JSON仍须独立验收。

### 2026-11-02 功能输入模板与8192预算修复

本次基于dev的d4507aa实施；提交与集成状态以Git为准。新增12项回归先复现固定128截断、观察缺失、长度上界遗漏和辅助文件不在准备清单的问题，再验证原生渲染/无thinking开关/身份变化/文件新增/容量边界/完整结束与公开计划保护。全套258项通过，无跳过；228 Python、42 JSON、2 shell语法、20个CLI help及73份文档检查通过，完整回归包含搬迁与公开入口检查。结构0 errors、168 warnings（3项新增软提示、1项原编排软提示解除），未放宽阈值；职责与保留理由见实现记录。

原服务器固定镜像与Qwen3.5-27B，使用完整314文件资源副本和新的尝试目录。客户effects通过公开functional_inputs观察生成26token输入；精度输入最大仍为366，统一预算为8558。公开verify复用已核验身份的原服务，文本/Chat/SSE均返回42，实际输入26、输出3tokens，finish_reason=stop，SSE包含DONE和usage；expected_prompt_tokens与实际用量一致。Agent读取三项原始请求和完整响应后调用公开review，七类必需结论全部pass，success=true。没有降低完整结束标准，也没有把旧失败回执改写为通过。

随后通过原owned交接公开停止服务，新任务精确核对并替换已退出同名容器。完整客户入口的新建服务再次通过三项功能检查，结果同为42、26输入/3输出tokens、stop；独立review与execute finalize均通过，迁移阶段completed。功能验证同时覆盖复用服务和新建服务，后续精度、交付和性能仍分别判断。

续跑精度时，C-Eval accountant的15题全部完成，原生accuracy为0.9333，覆盖完整、截断0、evaluation_validity=pass。advanced_mathematics仅2题取得完整stop响应，第三题期间容器收到停止信号；Docker事件显示SIGTERM后20秒SIGKILL，退出137、OOMKilled=false。后续只读归因检查未找到请求与调用进程的关联记录，具体发起者仍无法确认；不将137直接解释为内存不足或归责于某个登录会话。该子集失败，GSM8K随后因同一服务已退出而未进入推理；客户结果blocked，未完成三个JSON导出或性能实验。另有只读性能交接预检确认：本次vLLM解释器在容器内，绑定的EvalScope在宿主机，现有性能Manager直接调用本地解释器，当前路径不能直接交接；本补丁未扩展该执行边界。上述运行中断与交接限制均独立于已通过的功能修复，保留原始证据，不自动重启或扩展源码修复。

首次观察中对辅助文件集合的处理已通过通用盘点修正；首次borrowed验证缺原启动日志引用，按原owned回执补齐后重跑。这两次记录均保留，不能将配方遗漏归为模型失败。功能修复已取得真实验证；后续完整客户精度、三文件导出和性能流程另记独立回执，不由本节功能结果推导成功。

## 路径兼容回归

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -v
```

路径和公开入口回归使用标准库核对以下内容。上述全量发现命令还会运行任务质量对齐及运行时采集用例，CPU 张量用例使用 PyTorch。

- 布局索引中 14 个 Skill 的入口、共享 Schema、当前资源和实际摘要。根及子 Skill 的直属资源归属唯一。
- 无构建清单时可执行，PATH 为空时准备计划仍成功；不依赖 Git、插件构建或新增安装。
- 新增源码/参考文件自动纳入，内容变化更新实际摘要，删除非必需资源不留下旧名单；缺入口、Schema、固定配方或模板明确失败。
- 某任务入口缺失只阻塞该任务，独立数据任务仍完成；完整注册资源副本则要求全部声明入口齐备。
- 角色外输出和维护文件不复制；缓存/日志排除，未知类型、未知嵌套 Skill、链接、特殊文件及路径越界拒绝。
- 22 个公开入口均检查 `--help`。搬迁测试覆盖全部主入口的计划，以及辅助入口和客户模式的计划用例，包括不同工作目录、含中文或空格的路径和重命名后的总根。
- 全部主入口检查总根内 `scripts/`、`references/`、`assets/` 和 `skills/` 的写入保护，计划模式保持运行目录未创建。
- 两条注册复制链路使用相同集合，新增源码自动复制；复制期间源内容、目标内容或文件集合变化均拒绝，旧平铺资源仍可复制并核对。
- 本地合成 metadata 和 JSONL 数据走公开准备 CLI，核验当前资源证据；再次运行时保留旧回执原始字节，不重写历史。
- 容器注册由真实传输模块保存请求、静态资源及来源映射，接入真实迁移结果复用校验和导出 CLI，核对三份 JSON 内容、集合及 SHA256；原注册证据字节保持不变。
- 注册请求缺失、内容改变、容器/回执来源映射或公开计划不一致时拒绝续接和发布；静态资源目录中的旧位置副本不能绕过校验，同主机注册规范化仍兼容。
- 缺失、null、整数 -1 和空列表按各字段的契约处理。覆盖模型身份和名称、容器名、设备候选池、冻结记录、prompt 冲突、仓库顺序及网络失败分类。`accuracy.gpu_baselines: []` 触发与省略基线相同的选择逻辑。
- 普通精度测试核对指定子集前 N 条样本、原生子集发现、数据元文件排除、服务上下文及 8192 输出预算。任务质量复测按自己的原生配置核对样本范围和输出预算。
- 有效精度项复用、语义待复核时不重启服务、已退出同名容器自动精确替换及其他状态的授权身份绑定、三文件真实生成及联合校验、同级整体发布和失败不发布半套。

计划测试将静态模板复制到临时请求，只检查形状与路径边界；占位引用不作为可执行业务输入。实际准备测试使用临时合成内容，不下载模型或评测集。容器注册续接回归使用合成准备、部署和基线回执，只模拟 Docker 边界；文件校验、迁移复用和导出发布不打桩，三 JSON 的 ready 仅证明该合成链路通过。未操作真实容器、服务、NPU 或评测 SDK。POSIX 特殊文件回归当前可运行，其他系统会明确显示 skipped；本次没有跳过测试。取消/超时专项、完整编排协议、宿主发现、其他 Python/操作系统及目标环境另行验收。

仅复核容器注册续接与导出：

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_container_registration_continuation.py' -v
```

## 静态语法检查

```bash
python3 -B - <<'PY'
import ast
import json
from pathlib import Path
import subprocess

root = Path('skills')
counts = {'python': 0, 'json': 0, 'shell': 0}
errors = []
for path in sorted(root.rglob('*')):
    if not path.is_file():
        continue
    try:
        if path.suffix == '.py':
            counts['python'] += 1
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        elif path.suffix == '.json':
            counts['json'] += 1
            json.loads(path.read_text(encoding='utf-8-sig'))
        elif path.suffix == '.sh':
            counts['shell'] += 1
            result = subprocess.run(['bash', '-n', str(path)], capture_output=True,
                                    text=True, timeout=15)
            if result.returncode:
                errors.append((str(path), 'bash -n', result.returncode))
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
        errors.append((str(path), type(error).__name__))
print({'checked': counts, 'errors': errors})
raise SystemExit(bool(errors))
PY
```

该检查不会导入评测配置、SDK 或加载模型；Python 模板只计入结构行数，不能据此声称模板生成和运行已验证。

## 公开入口检查

先阅读新增入口的源码确认 `--help` 没有外部效果，再逐项枚举入口并记录返回码。搬迁回归同时覆盖移除 `PYTHONPATH` 后的运行环境。

```bash
python3 -B - <<'PY'
from pathlib import Path
import subprocess
import sys

failures = 0
total = 0
for skill in sorted(Path('skills').rglob('SKILL.md')):
    for entry in sorted((skill.parent / 'scripts').glob('*.py')):
        if entry.name in ('skill_paths.py', 'service_address.py', 'model_cache.py', 'evalscope_installation.py',
                          'device_contract.py', 'allocation_contract.py', 'device_evidence.py'):
            continue
        total += 1
        try:
            result = subprocess.run([sys.executable, '-B', str(entry.resolve()), '--help'],
                                    capture_output=True, text=True, timeout=15)
            failed = result.returncode != 0
            detail = result.stderr.strip().splitlines()
            print(skill.parent.name, entry.name, result.returncode,
                  detail[-1] if detail else 'help available')
        except (OSError, subprocess.SubprocessError) as error:
            failed = True
            print(skill.parent.name, entry.name, type(error).__name__)
        failures += int(failed)
print({'entries': total, 'failed': failures})
raise SystemExit(bool(failures))
PY
```

上述独立枚举用于快速检查原目录，完整路径回归由本页测试命令执行。真实业务输入必须替换示例路径、零摘要和负载；本次静态计划不作为目标环境验收，取消和超时已有定向回归，完整编排专项仍待验收。

## 开发技能资源检查

下面只读取本仓资源，检查快照集合、摘要、名称、当前 frontmatter、引用闭包及 4 个 Python、1 个 shell 支持脚本语法。静态文件集合使用 Git 的已跟踪及未忽略候选文件，避免把本地缓存算入快照；必需文件被错误忽略仍会报错。完整 frontmatter 语义仍需技能审查；本检查支持当前描述的单行和缩进块写法，不宣称通用 YAML 验证。清单记录原权限，跨主机只在 POSIX 上核对可执行位，不强制完整权限位相同。

```bash
python3 -B - <<'PY'
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

root = Path('.agents/skills')
manifest = json.loads(Path('.agents/skill-sources.json').read_text(encoding='utf-8'))
assert manifest['schema_version'] == 1
names = manifest['direct_skills'] + manifest['supporting_skills']
assert len(names) == len(set(names))
actual_skills = {path.parent.name for path in root.glob('*/SKILL.md')}
assert actual_skills == set(names), '技能集合不一致'
assert not any(path.is_symlink() for path in root.rglob('*')), '技能资源不能是个人目录链接'
expected = {row['path']: row for row in manifest['files']}
assert len(expected) == len(manifest['files']), '资源路径重复'
listing = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard',
                          '-z', '--', root.as_posix()], capture_output=True, check=True)
actual = {os.fsdecode(path) for path in listing.stdout.split(b'\0') if path}
assert actual == set(expected), '快照资源集合不一致'
scripts = {'python': 0, 'shell': 0}
for name, row in expected.items():
    path = Path(name)
    assert path.resolve().is_relative_to(root.resolve()), '资源越界'
    raw = path.read_bytes()
    assert len(raw) == row['size'], name
    assert hashlib.sha256(raw).hexdigest() == row['sha256'], name
    if os.name == 'posix':
        assert bool(stat.S_IMODE(path.stat().st_mode) & 0o111) == bool(int(row['mode'], 8) & 0o111), name
    if path.suffix == '.py':
        ast.parse(raw.decode('utf-8'), filename=name)
        scripts['python'] += 1
    elif path.suffix == '.sh':
        subprocess.run(['bash', '-n', str(path)], check=True, timeout=15)
        scripts['shell'] += 1
dependencies = manifest['skill_dependencies']
assert set(dependencies) == set(names)
for name in names:
    text = (root / name / 'SKILL.md').read_text(encoding='utf-8')
    header = re.match(r'^---\n(.*?)\n---', text, re.S)
    assert header, name
    fields = dict(re.findall(r'^(name|description): (.+)$', header.group(1), re.M))
    description = fields.get('description', '').strip()
    assert fields.get('name') == name and description, name
    if description in ('>', '>-', '|', '|-'):
        block = re.search(r'^description: [>|]-?\n((?:[ \t]+[^\n]*\n?)+)',
                          header.group(1), re.M)
        assert block and block.group(1).strip(), name
    assert set(dependencies[name]) <= set(names), name
    assert all(dependency in text for dependency in dependencies[name]), name
print({'skills': len(names), 'resources': len(actual), 'python_scripts': scripts['python'],
       'shell_scripts': scripts['shell'],
       'dependency_closure': 'complete'})
PY
```

本轮另在包含空格和中文路径的临时审计资源副本中，以 Python `-I -B -S` 隔离模式显式加入副本脚本目录后运行审计，并检查导入来自该副本或标准库；兼测无 `tomllib` 时的策略解析。结果证明不依赖作者的个人技能目录或 site-packages，不证明实际运行于 Python 3.10、Windows 或同事电脑。

## 治理文档检查

下面检查根 README、根规则、docs、业务及开发技能 Markdown 的相对链接、标题锚点及围栏，并解析结构配置和基线。仅检查链接存在性，不执行链接目标，也不输出文档正文。当前文档采用内联链接和普通标题；未来增加复杂 Markdown 写法时应相应调整检查。

```bash
python3 -B - <<'PY'
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path('.agents/skills/codebase-organization-guardrails/scripts').resolve()))
from source_shape_policy import load_policy
from source_shape_baseline import load_baseline

policy_path = Path('.codex/code-structure-policy.toml')
policy = load_policy(policy_path)
baseline = load_baseline(Path('.codex/code-structure-baseline.json'))
assert baseline is not None, '结构基线缺失'
assert baseline['policy_sha256'] == policy.digest, '策略与基线身份不一致'
errors = []
documents = [Path('README.md'), Path('AGENTS.md'), *sorted(Path('docs').glob('*.md')),
             *sorted(Path('.agents/skills').rglob('*.md')), *sorted(Path('skills').rglob('*.md'))]

def visible(text):
    return re.sub(r'^```[^\n]*\n.*?^```[ \t]*$', '', text, flags=re.M | re.S)

def anchors(path):
    text = visible(path.read_text(encoding='utf-8'))
    seen = {}
    result = set()
    for heading in re.findall(r'^#{1,6} +(.+)$', text, flags=re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if count == 0 else f'{slug}-{count}')
    return result

for path in documents:
    text = path.read_text(encoding='utf-8')
    if any(line != line.rstrip() for line in text.splitlines()):
        errors.append((str(path), '尾随空白'))
    if len(re.findall(r'^```', text, flags=re.M)) % 2:
        errors.append((str(path), '未闭合代码围栏'))
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', visible(text)):
        parts = urlsplit(target)
        if parts.scheme or parts.netloc:
            continue
        resolved = (path.parent / unquote(parts.path)).resolve() if parts.path else path.resolve()
        if not resolved.exists():
            errors.append((str(path), target, '目标不存在'))
        elif parts.fragment and resolved.suffix == '.md':
            if unquote(parts.fragment) not in anchors(resolved):
                errors.append((str(path), target, '锚点不存在'))
print({'documents': len(documents), 'policy_version': 1,
       'baseline_files': len(baseline['metrics']['files']), 'errors': errors})
raise SystemExit(bool(errors))
PY
```

另行执行 Git 差异检查并分别记录退出码，不能用它的成功覆盖上一个文档检查的失败：

```bash
git diff --check
```

仍需人工核对规则无冲突、接口描述与源码相符、命令效果符合范围，以及 Git 审批没有被其他文档弱化。`git diff --check` 只覆盖 Git 所见差异；上面的文档检查也检查未跟踪新文档的尾随空白，内容仍需审查。

## 知识收尾盘点

运行仓库内只读脚本，输出文件路径、体量、规则候选及 Git/worktree 元数据，不输出文档正文：

```bash
bash .agents/skills/neat-freak/scripts/audit-inventory.sh .
```

脚本需要 Bash 和 find、awk、wc 等命令；Windows 原生 PowerShell 环境未验收，缺少工具时报告环境前置条件，不隐式安装。当前已核对正常盘点、中文/空格路径、有效与失效规则链接、无参数退出 64、目录不存在退出 66，以及盘点前后文件不变。该脚本不替代文档语义审查、运行态验证或删除授权。

## 结构审计与基线

工具由本仓 `.agents/skills/codebase-organization-guardrails/` 提供，直接使用相对仓库路径；来源和依赖见 [开发技能索引](DEVELOPMENT_SKILLS.md)。团队成员无需预装个人技能目录。缺文件时报告仓库副本不完整，不隐式安装或跳过质量规则。工具带 Python 3.10 的策略解析回退，不额外安装 TOML 依赖。

修改前后普通审计：

```bash
python3 -B .agents/skills/codebase-organization-guardrails/scripts/source_shape_audit.py \
  --repo-root . --policy .codex/code-structure-policy.toml \
  --baseline .codex/code-structure-baseline.json
```

退出 0 表示无阻塞发现，仍须审查 warnings；1 表示硬违规或应收紧基线，2 表示配置或输入错误。审计报告不证明依赖方向、语义内聚、行为正确或函数参数数。

首次初始化或已有改善后收紧基线使用以下**写入命令**，不得在普通验证中顺手执行。当前测试根 `tests/` 已登记。新增文件或度量改善时同步更新基线；刷新前先检查全部硬违规和基线退化，不得以写基线掩盖失败。

```bash
python3 -B .agents/skills/codebase-organization-guardrails/scripts/source_shape_audit.py \
  --repo-root . --policy .codex/code-structure-policy.toml \
  --baseline .codex/code-structure-baseline.json --write-baseline
```

生成后重新运行普通审计，并核对基线确实覆盖实际资源。禁止手工编辑指标或无批准上调硬热点；只有用户明确批准的例外才可以使用 `--approve-baseline-increase`，且需在 [代码质量](CODE_QUALITY.md) 记录依据。基线移除或政策豁免也不得成为绕过阈值的方法。

## 分层验收与交付

| 层次 | 相关改动需要的证据 |
| --- | --- |
| 规则与文档 | 链接、内容、命令、策略/基线及差异；按本次实际范围核验 |
| 离线业务 | 公开 CLI、输入/结果契约、路径搬迁与保护、失败隔离和取消；行为修改需重复验证 |
| 实际资产 | 本次来源、版本、文件/镜像身份及实际内容；不能用计划或下载退出码代替 |
| 部署功能 | 最终载体注册、真实权重、NPU worker、请求与输出、服务最终状态 |
| 原生精度 | 原配置、数据/样本选择、HTTP 与原生产物、指标/单位、有效性、可比性和目标结论 |
| 宿主调用 | 所选宿主实际发现、选择和调用，不从嵌套目录推断 |

单独的文档修改不能消除 CLI 故障；失败时保留原结果并继续独立检查。本次路径修复的 CLI 结论来自上面的实际回归。缺少实机环境明确标记未运行，不能写成通过或零项通过。

本轮及后续纯治理变更还要逐文件比对 `skills/` 的集合、SHA256 和权限，确认待提交内容已排除私有连接配置和真实凭据，并确认 main、dev 和远程跟踪引用没有被意外移动。提交前保留差异与检查结果，在获得 commit 授权前不提交；合并按第二次授权单独执行。规则加载的跨会话实际体验如未观察，应记为未验证，不用启动另一任务代替本轮验收。
