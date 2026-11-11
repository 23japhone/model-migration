# 模型迁移特性开发规划汇总

本文说明 `model_migration` 的需求来源、当前功能范围、输入要求和完成标准，核对日期为 **2026-10-28（Asia/Shanghai）**。目录、调用实现、资源边界和路径契约由 [ARCHITECTURE.md](ARCHITECTURE.md) 维护；具体字段与执行规则以各 Skill 的现行契约为准。

Codex 的开发与 Git 规则见 [AGENTS.md](../AGENTS.md)，完整治理文档从 [PROJECT_BRIEF.md](PROJECT_BRIEF.md) 导航。当前检查结果见 [TESTING.md](TESTING.md#当前验证基准)，后续缺口顺序见 [ROADMAP.md](ROADMAP.md)；这些文件不另行定义需求，也不把本文的需求描述转化为自动实施授权。

**当前状态：12 个嵌套 Skill 保持原职责，客户输入、阶段请求生成、精度续接及三文件整体发布已实现并合入本地 dev，旧接口兼容能力保留。版本与离线证据见项目简介和验证文档；“纳入当前范围”不表示目标环境、模型或精度已经通过实机验收。**

## 1. 需求背景与文档用途

需求最初来自《模型迁移特性开发规划》图片的文字整理，涵盖期望输入、准备工作、模型迁移、精度对齐和 Skills 设计诉求。原始输入已在第 3 节完整列出，当前任务要求与原始规划取舍分别见第 4、6 节；维护者可直接在本文查阅所需的需求说明。本次结合现行技能契约整理，没有重新核对原始图片。

本文区分三类内容：

- **原始规划**：保留需求脉络，不把其中每一项当作当前待开发任务或实施授权。
- **当前纳入范围**：说明本套 vLLM / vLLM-Ascend Skills 应完成的业务及验收边界；迁入后的运行验证另行记录。
- **后续确认的补充需求**：来自现行契约，例如完整流程的三个交付 JSON，不追溯写成原图已经提出的要求。

本文负责“做什么、输入是什么、何时算完成”；架构文档负责“如何组织和调用、哪些实现问题尚未解决”。两者不重复维护详细目录树、脚本路径和逐文件修复清单。

## 2. 当前功能范围

本套 Skills 面向昇腾环境中的 vLLM / vLLM-Ascend，覆盖资产准备、支持度预检、按需模型实现适配、框架注册、部署功能验收、精度测试及三文件交接。用户可以请求完整流程，也可以直接使用独立阶段或任务。

当前只建设“精度对齐”阶段中的一级任务“精度测试”。测量、条件可比性和目标达标分别判定，不包含“标准三步走问题定界”和“逐层激活值诊断”。阶段名称沿用原规划，不据此扩大其能力。

SGLang 迁移与注册、框架自动选择、完整的运行异常分类诊断体系、自动修复重试、性能优化与正式压测、GPU 逐层 L1 诊断均不属于当前范围。原规划中对应条目在第 6 节保留，不因此成为必须补齐的依赖。量化、训练、多机调度和 Agent 宿主插件发布也不纳入本仓交付。

## 3. 输入要求与适用条件

原文列出的七类输入是需求清单，**不是所有请求都必须填写的统一表单**。实际必需项由所选任务与契约决定。

客户完整流程仅输入 prompt 与符合[客户模板](../skills/vllm-model-migration/assets/template.json)的 task.json；默认值及 Agent 自动补齐范围由[客户契约](../skills/vllm-model-migration/references/customer-task.md)定义。客户参数简化不代表底层阶段可以省略运行身份、资源预算或评测条件。

| 原始期望输入 | 当前处理方式 | 适用条件与限制 |
| --- | --- | --- |
| 模型链接 | 接受明确的模型来源、revision 或已有本地资源，核对配置、权重及文件身份 | 模型准备、部署等场景按任务提供；仅支持度预检可以先使用元数据，不强制先下载完整权重 |
| 测试集链接 | 明确数据来源或本地路径，以及 split/subset、样本选择和评分所需格式 | 精度测量需要有效数据；可以复用已核验数据，仅部署不强制准备测试集 |
| 模型部署框架 | 当前范围固定为 vLLM / vLLM-Ascend，记录本次实际版本和运行环境 | 不提供 SGLang 路线或自动框架选择，不把框架名称当作兼容性证明 |
| GPU 启动参数 | 作为基线溯源和配置对照资料，保留原始参数及 NPU 侧调整依据 | 不是所有 NPU 部署的前置条件，不能原样拼成 NPU 启动命令；参数本身也不等于有效 GPU 精度基线 |
| 精度目标 | 明确指标、单位、方向及适用的绝对目标或相对基线条件 | 声明达标需要明确目标；无目标仍可取得有效测量，但目标结论保持未评估 |
| 性能目标 | 可以原样记录，当前性能结论为 `not_evaluated` | 不执行性能达标验收；客户交接负载由 Skills 自动生成，旧导出接口继续接受明确候选 |
| GPU 逐层 L1 norm 值 | 当前不要求、不消费 | 属于未纳入的逐层诊断方向，缺少它不阻塞现有准备、迁移或精度测试 |

完成实际任务还可能需要目标主机、运行载体、设备与归属、镜像身份、软件版本、资产位置、证据工作根、评测解释器和工具配置。这些是现行契约的执行条件，不宣称全部来自原图，也不统一提升为总入口开始前必填。

缺少 GPU 基线不必阻塞 NPU 单端测量；如果存在独立绝对目标，可以判断该目标，但不能宣称 GPU/NPU 已经对齐。输入、示例和历史配方中的数值不能未经确认成为本次默认参数。

## 4. 三阶段功能需求与完成标准

### 4.1 准备工作

原规划的“镜像下载、权重下载、测试数据集下载”在当前范围中统一表述为**资产准备**，包含获取、复用、核验和身份记录，不要求每次重复下载。

| 任务 | 对应 Skill | 业务完成要求与边界 |
| --- | --- | --- |
| 模型资料与权重准备 | [vllm-model-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-model-prepare/SKILL.md) | 按请求范围取得或复用元数据、tokenizer/processor、权重及索引，记录固定身份；文件齐备不代表目标框架已支持或推理通过 |
| 镜像准备 | [vllm-image-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-image-prepare/SKILL.md) | 拉取、加载或复用指定镜像，核对实际身份、平台及来源资料；不在准备任务中启动模型服务 |
| 测试数据集准备 | [vllm-dataset-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-dataset-prepare/SKILL.md) | 取得或复用数据文件，固定版本、split/subset 和实际样本选择；不负责运行评测器或评分 |

由 [vllm-migration-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/SKILL.md) 选择本次必需任务、核对版本资料与子任务回执，交付资产和证据引用。仅部署需要的资产与精度测试需要的数据分别判断；某一独立任务失败时保留其他有效结果。

准备完成须满足本次必需资产及适用资料的契约要求。模型或镜像文件存在、下载命令退出成功、版本资料判断通过，都不能单独作为真实 NPU 运行证明。

### 4.2 模型迁移

**模型迁移**的目标是让模型在目标 vLLM / vLLM-Ascend 和昇腾 NPU 上加载实际权重、接受请求并产生满足本次功能要求的结果。已有支持模型主要需要配置、部署和验证；新架构可能需要额外的模型实现或权重加载适配。

**框架注册**是建立模型架构与实现代码的对应关系，使框架能够找到应加载的实现。它可以是迁移的一个环节，但注册成功不等于权重加载、推理或功能验收通过。

| 任务 | 对应 Skill | 业务完成要求与边界 |
| --- | --- | --- |
| 支持度预检 | [vllm-model-precheck](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/vllm-model-precheck/SKILL.md) | 读取模型配置，结合目标版本实现和昇腾证据给出支持候选、注册需求、适配需求或阻塞；静态支持候选仍需后续运行验证 |
| 模型实现适配 | [adapt-agent](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/adapt-agent/SKILL.md) | Agent 按公开流程完成必要实现、权重映射、定向检查和适配回执；迁移 CLI 消费回执，不自动生成模型代码 |
| 框架注册与检查 | [vllm-model-register](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/vllm-model-register/SKILL.md) | 完成必要映射变更或复用核验，提供初检和最终载体复验入口；不能靠改模型架构名掩盖不兼容 |
| 部署与功能验收 | [verify-agent](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/SKILL.md) | 把适配/注册纳入实际运行载体，核验真实模型、服务、请求和输出，交付证据与服务状态；不以 HTTP 200 或非空文本替代功能验收 |

[vllm-migration-execute](../skills/vllm-model-migration/skills/vllm-migration-execute/SKILL.md) 根据预检和既有回执编排迁移。已有支持的模型可跳过实现适配或写注册，但不能跳过必要的载体核验和真实功能检查。需要语义复核时保留原始输出与待复核状态，通过明确的 review/finalize 流程继续，不自动判定语义通过。

迁移完成不表示正式数据集精度或性能已达标。服务生命周期归 `verify-agent`；借用服务保持运行，停止本次拥有的服务须符合实际请求、消费者依赖和资源身份核验。

### 4.3 精度测试

[vllm-precision-align](../skills/vllm-model-migration/skills/vllm-precision-align/SKILL.md) 负责组织输入、委托和汇总，[verify-agent-plus](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/SKILL.md) 负责原生评测或已有产物分析。部署、数据准备和精度评分保持各自唯一归属。

当前要求如下：

- 新测量默认使用 EvalScope，兼容显式 AISBench 和旧 Python 配置；工具按现行任务契约识别与校验。明确版本按精确期望核验，省略版本或使用 `latest` 时才在实际运行环境核验官方最新稳定版，不把所有请求改成强制 latest。
- 使用实际工具原生配置、模板和评分，保留数据、样本、生成条件、结果、日志和指标来源。客户 EvalScope 每次准备调用原版安装脚本，默认主机共享 /data/evalscope-venv；可选参数仅进入 Agent context。实际环境变化要求新任务；AISBench/资产 SDK 和旧内部请求的环境前提保留，不切换工具。安装检查不替代原生精度验收。
- GPU/NPU 比较必须核对模型、权重、数据及关键评测条件；不同工具的分数不能直接当作相对基线，指标单位不能混用。
- 测量有效性、基线可比性和目标达标分别判定。有效评测未达到目标时可以完成任务并交付 `fail` 结论，不能改写成通过，也不自动进入诊断或修复。
- 缺少数据或评分配置时不能用任意聊天请求代替精度测试；缺 GPU 基线不宣称对齐，缺目标不宣称达标。

原图中的“通过 AISBench 和开源数据集测试精度”保留为来源背景，不作为当前工具唯一选择或新仓验收事实。具体配置及工具支持边界见[精度任务契约](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/references/contract.md)。

## 5. Skills 设计要求与后续补充需求

### 5.1 组织与调用要求

保留“总入口 → 阶段 → 独立任务”三层结构，当前对应 **1 个总入口、3 个阶段、8 个任务，共 12 个 Skill**。按可独立验收的业务拆分任务，不把读取 JSON、计算摘要等内部操作另建成技能，也不将原规划每一行都机械对应为一个新 Skill。

[vllm-model-migration](../skills/vllm-model-migration/SKILL.md) 负责选择范围、核验依赖与交接、汇总执行结果；阶段和任务可以按需要独立调用。独立调用不等于单独复制一个子目录即可运行，共同契约、路径支持和跨技能资源必须完整保留。

技能运行环境独立于 MoFix，代码仓用于维护资源，不要求启动 MoFix 后端。供 Codex、Claude Code 等 Agent 使用是设计目标；每个宿主的发现、选择与执行需要单独验证，不能根据目录存在推断兼容性已通过。本仓采用 skills 形态，不增加宿主插件注册、安装发布或后台调度系统。

### 5.2 后续确认的使用场景

现行契约除完整流程外，还保留仅准备、仅部署、已有服务精度测试、独立任务和历史结果分析。已有资源须按任务契约重新核验后复用，不因进入完整流程就重复下载或部署。历史 `analyze` 按来源工具和版本分析已有产物，不查询当前最新版本、不产生新推理或重新评分。

这些场景按现行[总入口契约](../skills/vllm-model-migration/references/contract.md)组织，不宣称全部在原始图片中明确展开。

### 5.3 完整流程的三个交付 JSON

客户 prompt + task.json 入口、精确默认值、自动阶段请求和同级三文件生成由[客户契约](../skills/vllm-model-migration/references/customer-task.md)定义。客户不填写性能负载；customer-baseline-v1 是明确初始配方。EvalScope 为本轮完整交付范围，显式 AISBench 精度执行保留、三文件交付不支持。实现及验收边界见[本轮实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md)。

这是原规划之后确认并已实现的交接要求，不追溯成原图条目。路径兼容及客户交付生成已有离线证据；真实模型、精度与三文件完整交付仍须在授权目标环境验收。

完整 `deployment_precision/run` 流程在部署功能验证和有效精度基线之后，须通过独立导出入口交付：

| 文件 | 用途 |
| --- | --- |
| `benchmark_plan.json` | 提供明确的服务与负载配置，不在本仓执行性能测试 |
| `accuracy_plan.json` | 固定本次基线对应的评测配置与数据选择 |
| `deployment.json` | 表达与实际部署证据一致的 vLLM 参数和运行环境 |

总入口阶段完成不代替末端导出。须由不带 `--plan` 的实际导出返回 `status=ready`，并包含三份 `deliverables` 的路径和 SHA256，才能确认交接已发布；计划模式的 `status=not_run`、`ready=true` 不表示文件已经生成。仅部分阶段、独立任务和历史分析不强制生成这套交接。缺少候选配置或负载信息只阻塞导出，不抹去前序有效结果，也不自动重跑部署和评测。

文件相对路径、当前导出支持的基线形式、配置映射及兼容限制唯一维护在[三文件交付契约](../skills/vllm-model-migration/references/tuning-handoff.md)。性能目标与负载配置是不同输入，不能使用示例数值补齐缺失配置，也不能因生成了交接 JSON 就宣称性能已经优化。

## 6. 原始规划与当前范围对照

以下条目保留需求来源，不表示已纳入实施、承诺以后开发或成为当前必需依赖。

| 原始规划内容 | 当前处理 |
| --- | --- |
| SGLang / vLLM 框架选择 | 当前固定 vLLM / vLLM-Ascend，不提供通用选框架能力 |
| SGLang 模型迁移与框架注册 | 未纳入当前技能树及执行路线 |
| 模型、镜像与测试集下载 | 纳入资产准备，支持按契约复用并核验，不限于重新下载 |
| 模型支持度预检、vLLM 迁移与注册 | 纳入当前业务职责，按任务证据分别验收 |
| 数据集精度测试 | 纳入当前范围；默认 EvalScope、兼容 AISBench，比较与目标分别判定 |
| 标准三步走问题定界 | 当前排除；原文的 prompt 渲染、反解及原始生成分段方案保留为背景，不补造原图未展开的九类结论 |
| 逐层激活值诊断与 GPU L1 norm | 当前排除；不要求安装或接入 `universal-precision-probe`，不把“任意权重模型、全量 hook、零源码改动”写成已验证能力 |
| 性能目标 | 允许记录为未评估；不扩展为性能优化、正式压测或达标验收；现行文件交接另按第 5 节处理 |

### 6.1 原规划中的异常类别

原表列举了异常类别和识别线索。保留它们有助于理解需求背景，但不能把“记录错误类别”擅自扩写成自动诊断、修复和重试承诺。

| 原始类别 | 原文关注的线索 |
| --- | --- |
| 后端算子缺少实现 | 算子未注册或未实现错误 |
| 输入组合不支持 | dtype、shape、layout、量化参数等不受支持 |
| 动态库或版本不匹配 | 缺少符号、库加载失败、接口版本或环境冲突 |
| 编译或图模式失败 | 编译、tiling、图捕获或回放错误 |
| 设备执行异常 | 超时、非法访问或设备故障 |
| OOM、HCCL 异常 | 内存或通信错误 |
| 非预期 CPU 回退 | 回退告警及实际执行记录 |

当前保留输入校验、原始错误、真实阻塞原因、超时取消、部分成果和依赖失败隔离；这些基础行为不等于已实现上述完整分类诊断体系。遇到错误不自动换模型、修改量化、升级环境、重试或清理其他任务资源。

### 6.2 评测期间采集逐层数据的可行性

截至 2026-11-11，`verify-agent-plus` 通过 HTTP 向已核验的模型服务发送评测请求，保存请求、回答、原生预测和逐样本评分。服务归 `verify-agent` 管理。精度任务会核验 worker 身份，逐层张量采集仍属范围外调研，本仓尚未接入 MindIE 探针。见[精度测量契约](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/references/contract.md#结果与生命周期)。

本次核对 [mindie-agent/debugger 固定提交 `831e03d`](https://github.com/mindie-agent/debugger/tree/831e03d64de24bda35a90f88e7b7dc8fe531e014)。下表记录可用于逐层诊断的上游能力。

| 采集位置 | 输入与输出 | 使用边界 |
| --- | --- | --- |
| [ACLGraph 模块输出](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/aclgraph.py#L328) | 在图捕获前挂接模型模块，重放时采集匹配模块输出中的首个张量 | 默认选择叶子模块，循环保留 8 个槽位，每个快照最多取前 16384 个元素并转为 FP32。读回快照需要设备到主机复制，vLLM Inspector 的步号读取也会同步 |
| [张量观察点](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/precision/npu_watch.py#L176) | 对指定张量记录统计值，出现 NaN、Inf 或越过阈值时锁存有界快照 | 调用方选择张量和观察位置。设备端环形缓冲区的记录需显式读回 |
| [模块调用前后对照](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/precision/lockstep_diff.py#L1) | 在模块调用前后记录输入、输出和可达状态，按模块、调用序号和 rank 对照基线 | 快照读取会同步设备。启用 healing 会用基线状态或输出替换现场值，适用于独立诊断 |
| [单次算子或函数调用](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/precision/call_repro.py#L94) | 包装选定调用，保存实参、返回值、可达状态的前后快照及 RNG 信息，供复现 | 采集粒度由调用边界决定。融合 kernel 内部中间值需要单独的可观察点 |

若后续接入，部署侧需要在执行模型的各个 worker 安装采集点。评测侧需要把样本与服务请求、rank、前向步及批内有效 token 位置对应起来，再将原生评分关联到选定层或算子的记录。持续导出需要处理槽位复用，并标明实际覆盖、截断和丢失情况。实机验收仍为 pending，需在固定模型和软件版本上分别核对 Eager、ACLGraph 的样本归属，以及开关采集前后的输出、延迟和显存占用。

## 7. 当前迁入状态与验证边界

截至 2026-10-28，新仓保留完整 12 个嵌套 Skill，当前资源数量见 TESTING.md。支持文件位于总 Skill 的 `scripts/` 和 `references/`，导入、索引、资源归属、输出保护及不依赖构建清单的资源枚举/复制已通过本地回归。现行路径契约见[架构文档](ARCHITECTURE.md)，本文件不另维护重复清单。

客户改造已合入本地 dev，版本见[项目当前状态](PROJECT_BRIEF.md#当前状态)。EvalScope 客户流程交付三个 JSON，显式 AISBench 保留精度执行且不支持三文件交付。已实现的默认值与交付生成不再列为待开发项，剩余实机、宿主和专项验证见[路线图](ROADMAP.md)。

迁入前的插件开发包验收、Git 提交合并和归档记录不作为本仓的完成证明，也不代表本仓当前版本或 Git 状态。本仓后续验收应绑定实际代码版本、执行环境和本次证据，并在本仓文档中记录结果与未覆盖范围。

文件复制与文档核对不证明 CLI 可运行、宿主可发现、目标环境可用、模型功能通过或精度达标。当前 CLI 结论来自实际离线回归；后续仍按任务分别取得资产、运行载体、模型/NPU、精度及宿主证据。单端固定样本或旧实测不能外推全量数据、GPU/NPU 对齐或任意模型支持。

本文整理需求与范围，不授权自动开发范围外功能、连接服务器、安装环境或启动模型任务。运行请求、实现行为与本次证据出现差异时，明确记录差异，并在对应契约或架构说明中维护当前答案。
