# 代码质量

本文是本仓唯一的源码组织和代码增长规则权威。业务组织事实见 [架构](ARCHITECTURE.md)，接口定义见 [接口索引](INTERFACES.md)，执行与验证分别见 [工作流程](WORKFLOWS.md) 和 [验证](TESTING.md)。规则初始化不授权源文件搬迁或架构改造。

## Source Tree Contract：源码组织契约

业务源码采用按能力组织的嵌套 Skill 结构，业务根为 `skills/`；仓库级开发技能根为 `.agents/skills/`，其中支持脚本也纳入源码审计。测试根为 `tests/`，路径兼容回归位于 `tests/vllm-model-migration/`。Skill 的 `scripts/` 保存实现，`references/` 保存契约，`assets/` 保存模板。`adapt-agent/reference/` 沿用实际单数目录，不为统一拼写而搬迁。

| 所有者 | 责任及内部边界 | 允许的跨边界依赖 |
| --- | --- | --- |
| 总入口 `vllm-model-migration` | `coordination` 保留旧编排；`customer_workflow` 归一化客户输入、逐步编译请求与续接；`tuning_handoff` 生成、核验并发布三文件 | 阶段公开 CLI、固定结果和契约 |
| 客户默认解析 | customer_workflow/defaults 的 policy 拥有纯默认规则，model/devices 拥有证据解析，image 绑定准备阶段返回的镜像身份，previous_image 核验旧任务冻结请求，state 拥有逐项冻结 | 当前客户模型身份门禁、既有 effects 公开委托、coordination 证据；Quay 选版和 Docker 缓存核验分别归准备阶段和镜像任务，见[验证记录](TESTING.md#2026-11-05-客户缺省镜像) |
| 准备阶段与三个准备任务 | 阶段选择资产和复核回执；任务分别拥有模型、镜像、数据获取与校验 | 固定任务 CLI；总入口共享静态资源 |
| 迁移阶段与四个任务 | 阶段按路线委托；precheck 判路线，adapt 管源码适配，register 管注册，verify 管载体与功能验证 | 固定任务 CLI 和交接；注册载体检查使用 register 公开入口 |
| 精度阶段与精度任务 | 阶段组织依赖和汇总；verify-agent-plus 拥有评测、原生结果和历史分析 | 准备/部署公开入口、已有交接；评测 SDK 在指定任务环境中使用 |
| EvalScope 共用安装契约 | 总 Skill scripts/evalscope_installation.py 拥有参数与路径校验；scripts/install_evalscope.py 为原样来源副本；准备 customer_assets/evaluator.py 拥有调用及结果适配 | 客户 context 与准备 CLI 共享参数契约，跨 Skill 仍通过公开 CLI；不导入兄弟私有实现 |
| EvalScope 客户续接 | customer_workflow/evaluation 的 history/selection 拥有选择证据和继承，probe 拥有只读前检，lifecycle 拥有正式/预览及绑定状态 | context 合成一次安装计划，effects 委托；调用仍经过公开准备 CLI；安装协议和续接测试统一归 evaluation 子目录 |
| EvalScope 性能环境核验 | customer_workflow/evaluation 的 handoff 校验来源，handoff_probe 拥有只读效果，handoff_cli 组装独立尝试 | 复用同包 history、coordination 证据与原安装脚本选项常量；不导入兄弟 Skill 私有实现、不调用安装或性能业务 |
| 共享路径与静态契约 | 总 Skill 的 `scripts/skill_paths.py`、结果 Schema、布局、资源枚举与快照规则 | 各层可读取；不反向调用业务流程 |
| 设备映射与证据 | verify 的 devices 拥有最终载体探针及宿主全卡观察；总 Skill 的 scripts/device_contract.py 拥有静态文件校验 | 精度经 verify 公开 inspect-devices；导出只读共享证据合同，不导入 verify 私有模块 |
| 共享 NPU 预算 | scripts/allocation_contract.py 拥有纯逐卡预算和分配回执合同；verify/customer/shared_capacity 选择候选，verify/devices/allocation 复核动态容量 | 编译器、verify、导出消费静态合同；device_contract → allocation_contract → device_evidence 单向依赖，底层只负责文件摘要绑定 |
| 仓库治理 | 根规则、`docs/`、`.codex/` 及 `.agents/` | 面向维护者；开发技能仅依赖自身资源与已声明的工作流技能；不得加入业务运行资源或复制进模型运行载体 |

### 依赖与放置规则

- CLI、`cli.py` 和 runner 等组合入口负责解析、映射、组装和委托；网络、文件写入、Docker、子进程、HTTP 与评测 SDK 保留可识别的效果边界。已有注册载体复验属于明确的公开任务依赖，不扩展成任意跨层调用。
- 跨 Skill 禁止导入私有业务模块；使用公开 CLI、结果文件与摘要。共享 Schema、路径模块、`service_address.py` 的本机地址策略、`model_cache.py` 的官方 blobs 边界及 `device_contract.py` 的设备证据合同归总入口基础契约，不构成跨 Skill 业务依赖；消费者为部署、精度、模型资产核验及交接导出。新增依赖须检查方向与环，当前没有自动依赖边界检查器。
- 每个文件保持一个可解释的变更原因。出现第二个独立职责、效果边界或测试边界时再提升为包；不按行数机械拆分，不引入空层、浅包装或猜测未来的接口。
- 共享代码须有稳定语义、明确所有者和实际跨模块消费者。不得新建无归属的 `utils`、`helpers`、`common`、`shared` 或类型杂物桶；相似语句不自动意味着同一知识。
- 新测试在独立获准的实现任务中增加到 `tests/<skill-name>/`，按业务能力及公开行为组织；同时把实际已创建的测试根加入策略。不能先声明不存在的根，也不把运行证据当测试源码。
- 子目录 `AGENTS.md` 仅在语义模块有不同命令、契约或安全边界时新增，只写增量并由根规则索引；本轮不创建局部规则。

新增、移动或改变职责的文件，修改前记录 Change Shape Map：

| 路径 | 语义所有者 | 单一职责/变更原因 | 当前消费者 | 公开或私有 | 允许依赖 | 预期增长 | 放置依据 |
| --- | --- | --- | --- | --- | --- | --- | --- |

涉及广泛源码迁移或公共契约变化时，先提出可验证方案；不得用“顺便整理”扩大已授权范围。

## Code Growth Contract：代码增长契约

所有代码变更通过 `maintainable-code-guardrails`；职责、布局、依赖、公开边界或热点变化同时通过 `codebase-organization-guardrails`。数字是结构审查和阻塞信号，不证明设计正确。

| 指标 | 软阈值 | 硬阈值 |
| --- | ---: | ---: |
| 源文件行数 | 400 | 800 |
| 测试文件行数 | 600 | 1200 |
| 同目录直接源码文件数 | 10 | 16 |
| Python 函数复杂度 | 10 | 15 |
| Python 函数分支数 | 12 | 18 |
| Python 函数语句数 | 50 | 80 |

指标超过软阈值时，对受影响单元记录继续保留、局部提取或另立迁移任务的理由。超过硬阈值的新热点阻塞交付。已有硬热点记录基线，不得增加行数、复杂度、分支、语句、参数、职责或直接同级目录压力；降低或移除已有指标后同步收紧基线。

修改前后测量受影响文件、函数和直接同级目录。审计工具记录文件/目录及 Python 复杂度、分支、语句；函数参数数、语义职责与依赖方向需额外人工比较，不能宣称工具已经检查。现有工具对低于旧基线的指标也会要求刷新，按其实际结果执行，不扩大解释为所有指标只许下降。

### 机器策略、基线与当前审查决定

- [策略](../.codex/code-structure-policy.toml)使用 version 1，覆盖 `skills/` 和 `.agents/skills/` 内 `.py`、`.sh`、`.template`；JSON/Markdown 不作为源码复杂度指标，仍接受语法、链接及契约检查。缓存、Git、构建目录仅按审计工具默认排除规则处理，无业务或开发技能目录例外。
- [基线](../.codex/code-structure-baseline.json)必须用 [TESTING 中的命令](TESTING.md#结构审计与基线)生成，不能手工编辑。普通验证不得带 `--write-baseline`；不能通过覆盖基线让增长违规消失。
- 任务质量的软提示集中在公开归档核验、原生报告解析、跨子集逐题配对和相应测试。归档查找归客户入口，原生评测归验证任务，分差与目标归比较任务。各自保留模型或题库身份、样本覆盖和缺项处理分支，以便单独回归；跨 Skill 通过公开 CLI 交换证据。[任务质量验收](TESTING.md#2026-11-25-任务质量对齐单题验收)
- 当前基线体量、测试集合及审计结果统一见[验证基准](TESTING.md#当前验证基准)，本页不重复维护数量快照。EvalScope 环境复用改动纳入新增模块并缩小编排和准备 CLI 热点，未调整阈值或上调硬热点；软提示的保留理由见[实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-10-29-evalscope-环境复用)，基础改造的历史理由保留在该记录的结构审查节。
- 本次共用安装改造的 Change Shape Map、前后指标与软阈值理由见[实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-01-evalscope-共用环境安装)。原版脚本字节保持一致；未新增阈值豁免。
- 本次续接继承和安装前核验的职责图、失败边界、21 项新增回归及结构审查见[续接记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-01-evalscope-续接继承与安装前核验)。测试按同一环境能力归组以避免平铺目录超限，原阈值不变；新增选择合成与探测校验的软提示按实际职责审查。
- 保留现有职责和布局：开发工具以完整技能快照引入，软提示不触发无关重构；后续触及对应函数或目录时必须记录结构审查决定。完整提示由审计命令复现，不在文档重复保存会漂移的逐函数列表。
- 客户模型身份复核独立归属 customer_workflow/model_identity.py，编排只在准备和消费者边界委托检查。当前新增目录/编排软提示的保留理由及修改前后指标见[模型输入实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-10-31-模型名称与本地身份复核)，未调整硬阈值。
- 客户缺省输入的静态规则与动态证据归属 defaults 子包；原 customer_workflow 直接源码文件数保持 12，子包 5 个文件。新增发现与冻结不继续堆入 run；本轮软阈值审查及基线收紧见[默认值实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-01-客户默认值兼容与自动补全)。
- prompt 顺序收口保持现有模块：contract 拥有显式值校验及静态规则，context 唯一合成实际参数，runner 消费同一结果；快照不用于实际分支。增长指标、私有接口迁移和软提示审查见[顺序收口记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-01-prompt-合成顺序收口)。
- EvalScope 性能启动前核验归同一 evaluation 子包的 handoff/handoff_probe/handoff_cli；选择证据、探测效果和入口组装各自内聚。职责图、前后指标、失败边界和独立消费者验证见[核验记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-01-evalscope-性能环境交接核验)，不扩展安装或性能执行职责。
- 数据集 prompt 补值仍归 contract/intent/context：字段类型校验共用，完整条目与 prompt 部分条目要求分开；一次 compose 返回声明及冲突。没有新增生产模块或索引接口；结构指标、列表比较软提示及消费者验证见[补值记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-01-数据集-prompt-逐字段补值)。
- 数据准备与评测交接分别由准备布局、原生路径/样本核验、公开预检、阶段预检和客户正式 catalog 绑定模块负责，跨 Skill 仍走公开 CLI；增长指标、参数数与软提示理由见[交接记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-03-评测数据交接修复工作区实现)。没有修改结构策略或上调硬热点。
- 设备映射探测与全设备用卡检查归 verify/devices；精度委托公开只读动作，导出共用静态证据合同。职责图、原请求不变式、失败边界、增长指标及软提示理由见[设备映射实现记录](DEVICE_MAPPING_IMPLEMENTATION.md)。没有修改结构策略或上调硬热点，真实910B验证仍待独立验收。
- 空闲优先与共享部署的职责图、原独占限制、RED/GREEN、指标与审查结果见[共享实现记录](SHARED_NPU_IMPLEMENTATION.md)。新增两个静态合同文件供多个消费者使用，保留旧 device_contract 导出；未提高结构阈值或硬热点基线。
- 官方默认数据位置选择归 dataset_prepare/resolution，复用既有文件验收和 HTTPS 下载器；catalog 只返回 Agent 指引。新增可选字段的形状及路径保护保留在 request/results，参数和增长审查见[默认目录记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-03-官方默认数据目录优先复用)。没有新增软提示、跨 Skill 私有导入或策略例外。
- GSM8K 默认 main 元数据仍归 dataset_prepare/native_layout；未新增生产模块或修改客户选择规则。布局和SDK验证的职责图、增长指标及证据边界见[GSM8K记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md#2026-11-03-gsm8k-本地子集布局兼容)，未新增软提示或放宽阈值。
- 资源发现与实际摘要统一归属 `skill_paths.py`；资源角色、必需项、文件遍历、直属归属和快照分别由小函数承担。载体注册不混入资源选择，两条复制链路只保留自有效果与快照核对。
- 容器注册续接由 `continuation.container_registration()` 校验 attempt-local 请求和来源映射；相关回归只模拟 Docker 外部效果。当前测试集合和实际指标分别以验证文档及审计输出为准，不保留过期的目录数量快照。
- 任何硬基线上调须用户明确批准，并记录所有者、路径、指标、理由、影响、验证及批准依据，之后才可使用 `--approve-baseline-increase`。生成、第三方、迁移、Schema 或嵌入资源等名称不自动构成豁免。

### 修改与审查要求

保持领域名称和直接控制流，I/O、错误与修改可见。行为变更需自动测试或可重复的精确验证；纯文档修改用链接、内容和差异检查，不新增镜像实现的测试。提取职责须保护现有行为，不能借拆分改变公开接口。错误边界遵循工作流程，不用新增布尔开关掩盖多重职责。

代码发生变化时同步相关 Skill 指令、契约、示例、资源规则和证据；后续路径或资源变更仍需同等范围的行为验证。维护者文档、`.codex/` 和 `.agents/` 变动不进入业务运行资源；开发技能快照按 [开发技能索引](DEVELOPMENT_SKILLS.md)维护独立来源清单。未配置的 Ruff、类型检查、依赖检查或 CI 必须如实列为缺口，不自行安装。
