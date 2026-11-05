# 客户输入流程实现与验收记录

本记录保存客户输入与三文件交付改造的职责、结构审查和验收依据。客户只提交 prompt 和 task.json；Agent 负责意图、官方资料、环境配方及语义复核，脚本负责确定性校验、请求生成、执行和证据。真实设备执行仍待授权目标环境验收。

## 版本与证据范围

当前代码基线与集成状态见[项目当前状态](PROJECT_BRIEF.md#当前状态)，现役测试数量及命令见[验证基准](TESTING.md#当前验证基准)。各轮提交和合入过程由 Git 历史追溯，本页不另维护一份“最新版本”快照。

以下日期章节保留各轮实施时的职责、验证和权限快照。其中“当前工作区”“未提交”“未同步”均指该章节记录时点，不表示当前 Git 状态；旧输入字段和旧 EvalScope 环境策略以现役客户及安装契约为准。历史命令和基准不自动升级为当前实机结论。

## 2026-10-31 模型名称与本地身份复核

本轮从本地 dev `d30783c9c61ee95b55a8a6d74636dbbcdddf860a` 创建 `codex/model-input-identity`；本节保留提交前实现与验证快照，提交及合入状态以本地 Git 历史为准。客户 JSON 严格收敛为 `model`、`environment`、`accuracy` 三组及模板子字段。模板完整保留对应需求示例的三组值与数据集顺序，不把示例值作为运行时默认。拒绝旧 served_model_name、vllm_ascend_image、evaluator_python、deployment 分组和旧客户快照；prompt 同样只能引用新字段，不保留别名转换。独立阶段的公开请求和原生配置字段继续按各自契约使用。

model_name 统一用于任务记录、结果标识、展示、vLLM 的 served_model_name 和功能/精度 API model。仓库 ID 与本地路径同时提供时，resolver 返回本地内容身份与待复核状态；Agent 核对真实来源材料，提交绑定仓库、路径、文件清单摘要和证据引用的 model_identity_review。脚本检查引用和绑定，match 后仍使用 source=local 与 expected_identity；不依据名称猜测一致，不自动重新下载或覆盖。语义判断仍归 Agent，文件摘要不是来源认证。

environment 使用 image_name、container_name、available_npu_devices。当时设备列表必须明确给出，表示允许使用的范围；范围内选择满足容量的最小 TP，不能扩大范围或强制占用全部卡。后续默认设备池与空闲优先共享策略以[当前契约](../skills/vllm-model-migration/references/customer-observations.md#空闲优先与共享部署)为准。host/port 由内部策略采用 127.0.0.1 和从 8000 起探测的空闲端口，写入实际部署请求。评测环境自动发现、优先复用、冻结及续接重验继续保留；Agent 可提供内部候选，不增加客户输入字段。

本轮 Change Shape Map（路径相对总 Skill，测试相对仓库）：

| 文件/范围 | 所有者、消费者及可见性 | 责任、依赖和增长依据 |
| --- | --- | --- |
| scripts/customer_workflow/contract.py、intent.py、context.py、state.py | 总入口输入及证据，客户 CLI/prompt/续接，私有 | 严格三组字段、当前 prompt 字段及原样快照比较，删除兼容转换；复用标准库及公共文件引用 |
| scripts/customer_workflow/compile/{assets,deployment,accuracy}.py | 客户请求生成，原阶段公开 CLI 的消费者 | 生成新字段对应的模型、镜像、容器及 API 请求；设备范围过滤/分配结果检查留在部署编译器，不导入兄弟 Skill 私有实现 |
| scripts/customer_workflow/model_identity.py | 总入口模型复核，prepare 消费，私有 | 独立的证据绑定/判定门禁，复用公共文件引用与既有人工决策类型；66 行 |
| scripts/customer_workflow/runner.py、effects.py | 客户编排及公开操作适配，CLI 消费，私有 | 准备及复用前检查身份；仅委托公开 CLI，自动评测环境选择；独立数据仍执行，当前诊断替换旧待决事项 |
| 模型任务 scripts/model_prepare/customer.py | 资产发现，公开 resolve.py 消费，私有 | 复用完整性核验，返回身份待复核状态；没有增加联网或下载效果 |
| tests/vllm-model-migration/ 客户相关测试 | 输入、资产、运行资源、请求、恢复与身份验证，unittest | 新结构及旧结构拒绝、API 名称、真实合成资产/引用、设备池边界、自动环境选择及失败隔离 |

结构审查：初始实现前 contract 148 行、intent 43 行、runner 263 行、模型 resolver 108 行，customer_workflow 11 个直接文件，审计 0 errors / 159 warnings。最终 contract 115 行、intent 44 行、runner 255 行、部署编译器 138 行。新增身份模块函数复杂度为 6/10/10；新增设备请求/分配检查复杂度 5/7。已有 runner.run 复杂度 15、分支 11、语句 32，均未增长；workflow 增加必要准备依赖检查，复杂度 9→11、分支 5→6、语句 16→17，仍只组装和委托，低于硬阈值。customer_workflow 12 个直接文件有明确职责，低于 16 的硬阈值，保留该软提示。最终普通审计 0 errors / 160 warnings（232 文件、39 目录、1404 函数），基线只纳入实际资源和度量、收紧改善项并移除已删除函数，未调整硬阈值或上调硬热点。

失败边界：非法/旧客户字段在外部效果前拒绝；缺复核或 unknown 由 Agent 继续取证，conflict 返回用户决策；模型分支失败阻止迁移、精度与导出，独立数据准备继续。复核、证据或资产改变拒绝复用；旧格式输入要求建立新任务，不改写历史。设备观察缺失或范围内容量不足阻塞迁移，不使用范围外设备。共享证据错误和取消沿用原边界。审查中修复了旧身份冲突决策残留的问题，当前诊断替换已过期决策，历史尝试文件保持不变。

验证：先以失败检查确认新模型字段/身份门禁缺失，再以新三组模板和设备范围测试复现旧四组校验及容量请求不匹配。最终 121 项标准库回归通过，无跳过；公开 CLI 接受新模板并完整保留其输入，旧 HEAD 模板以 invalid_input/退出 2 拒绝，均未创建运行目录。手工比较确认模板与需求示例的三组投影逐项相等，含字段、嵌套值及数据集顺序；检查脚本先解析 macOS 临时目录真实路径，以满足已有输出路径规则。静态、文档及结构结果见 [TESTING](TESTING.md#当前验证基准)。没有模型下载、依赖安装、真实服务、NPU 或原生精度运行；未写长期记忆，未执行跨仓同步。

以下记录保留此前版本的实施证据，不作为当前客户字段定义。

## 2026-10-29 EvalScope 环境复用

本轮从本地 dev `0a1eba5cb84a1581681436c6a2caf31165918603` 开发，提交为 `d2fd304bbda9ac8a88120e68a8c6bf0c7f507afa`，以 `--no-ff` 合并提交 `5fe40b19a484cdfa47511e574420c8a8d03e6fb9` 合入本地 `dev`；两者内容树一致。原主题分支 `codex/evalscope-environment-reuse` 已在用户授权后删除，未推送。main、origin/main 与本次查询的远端 main 均为 `0a1eba5cb84a1581681436c6a2caf31165918603`。本轮未连接服务器或运行设备；下文基础改造记录保留为 2026-10-28 的历史证据。

客户契约新增可选 `environment.evaluator_python`：指定执行主机 Python 绝对路径，整数 -1 或旧输入省略时有界发现。优先复用实际能力合格的环境；不因版本较旧查询 latest 或强制升级。只有明确缺依赖才新建任务隔离环境，现有版本和依赖约束固定。旧字段严格类型、旧 --request、显式版本、AISBench 和 analyze 边界保持；新字段本轮只用于 EvalScope。

修改前的 Change Shape Map：总入口 contract/intent/state 拥有新字段和旧输入内存兼容，effects/runner 只经公开 CLI 委托；准备阶段 customer_assets 新增 discovery/evaluator，分别负责有界候选与选择/冻结/续接；精度任务新增公开 inspect_environment.py，拥有 SDK 能力检查，不导入兄弟私有实现；安装仍由 environment.py 管理。模型 resolver 只返回明确缺少的资产 SDK 与实际 Python，资产 SDK 不作为本地模型或评测环境的统一前置。新测试单独放在 test_evaluator_environment.py，不继续增加已有大型测试文件。

修改前相关体量：contract 125、effects 41、runner 242、state 78、intent 42、environment 115、customer CLI 63 行；customer_workflow 11 个直接文件，customer_assets 4 个，原生 evalscope 目录 13 个。新模块归属 customer_assets 和精度公开入口，没有向两个既有目录热点继续增加同级私有模块。原审计 0 errors、161 warnings。拆出发现编排后 workflow 复杂度 13→9、分支 10→5；customer CLI main 复杂度及分支 13→10；命令证据与安装分别保持独立责任。

失败边界：ready 直接复用；明确 missing_dependencies 才允许新环境；显式无效路径、不兼容和未知失败阻塞；自动候选某项失败仍检查后续已有候选，没有合格候选且存在不明失败时禁止安装。评测失败不跳过本地模型发现；数据/模型问题不触发评测重装。超时及取消回收的日志先保存，安装超时发布 blocked resolution；共享证据失败不能伪装成缺依赖。

验证台账：先补回归，11 项在旧实现上出现 2 failures/9 errors，确认缺少字段、复用入口、独立模型发现和超时证据；实现后通过。随后补充旧快照、失败分类、SDK 分离、输出保护、公开 CLI 和真实进程边界测试。最终 102 项本地测试通过，其中新增 26 项；没有跳过。临时 venv 无 pip、合成 SDK 无下载，公开 CLI 复用前后原环境文件一致；修改 EvalScope 源码后续接被拒绝。详细命令统一见 [TESTING](TESTING.md)。

审查已处理的问题：保留 venv 启动路径避免解析到基础 Python；将资产 SDK 的版本缓存与旧评测计划区分；自动发现的超时候选不能遮蔽后续合格候选；不沿用未重验的 evaluator 观察；禁止任务输出落入显式借用环境；重复的 SDK resolver 尝试使用不同日志目录；旧模型回执缺少 asset_python 时保留原隔离 SDK 解释器；prompt 补齐的环境路径在首次任务台账写入前完成输出保护。后两项均先补失败回归再修正。当前无已知阻塞审查项。未改日志中的镜像元数据探测问题，未实现外部 end-to-end Skill 或性能执行。

新结构基线只纳入新增资源及已改善指标，未上调硬阈值；普通审计为 0 errors、159 warnings。新增软提示仅为测试目录 11 个文件，各测试职责明确，低于 16 的硬阈值；客户编排和准备 CLI 的原软热点减少。尚未验证真实 EvalScope/Transformers 组合、pip 解析/缓存、服务器发现、真实数据集及完整设备流程。借用环境不删除、不写任务回执；未推送或修改 main，未写代理凭据或个人记忆。

## Change Shape Map

以下为 2026-10-28 客户流程基础改造的历史记录。路径均相对 `skills/vllm-model-migration/`，测试归 `tests/vllm-model-migration/`。新增文件初始指标为零；修改前完整结构审计为 183 文件、35 目录、1143 函数、0 错误、143 提示。不得增加旧硬热点。

| 路径 | 所有者 | 单一责任 | 消费者/可见性 | 允许依赖 | 增长和位置理由 |
| --- | --- | --- | --- | --- | --- |
| scripts/customer_workflow/contract.py、intent.py | 总入口 | 客户类型及意图约束 | 客户 CLI，私有 | 标准库 | 独立客户输入语义，不改变旧请求 |
| scripts/customer_workflow/cli.py、state.py、runner.py | 总入口 | 参数适配、任务证据、增量执行 | migrate.py，私有 | 公开 Skill CLI、静态路径支持 | 新能力独立包，不扩大协调器热点 |
| scripts/customer_workflow/compile/ | 总入口 | 由实际观察生成资产、部署、精度请求 | 客户流程，私有 | 公共文件契约 | 分离三个不同请求责任 |
| scripts/coordination/cli.py | 总入口 | 选择客户或旧内部请求 | migrate.py，公开 | 两个流程入口 | 只加解析与委托 |
| scripts/tuning_handoff/generation.py、flat_publication.py | 交付 | 从有效基线生成候选；同级原子发布 | 客户流程/导出 CLI | 现有证据校验 | 不在旧发布函数堆布局分支 |
| scripts/tuning_handoff/cli.py、contracts.py、runtime_environment.py | 交付 | 接入生成及执行环境路径证据 | 旧/新交付 | 原部署和精度公开结果 | 保持旧字段与旧布局 |
| 准备阶段 scripts/customer_assets/ | 准备阶段 | 隔离环境与客户资产发现 | 客户流程经公开 CLI | 标准库、隔离 SDK | 网络和安装效果归准备所有者 |
| model-prepare scripts/model_prepare/customer.py | 模型准备 | 本地优先与有区别的仓库查找 | 模型公开 CLI | 本任务资产/供应商模块 | 复用既有完整性核验 |
| verify-agent scripts/verification/customer/ | 部署验证 | 主机资源、地址、端口、容器授权与路径证据 | 公开发现/生命周期入口 | 本任务 Docker 与进程边界 | 服务操作仍唯一所有者 |
| precheck scripts/model_precheck/source_identity.py | 支持预检 | Git 与实际安装包快照身份 | 预检请求/来源核验 | 标准库 | 不给 wheel 伪造 commit |
| 精度 runtime/service.py | 精度验证 | 校验本机服务地址 | 原精度运行 | 共享静态地址策略 | 与部署保持相同地址边界 |
| 客户模板、契约、Skill 和项目文档 | 各公开接口所有者 | 说明客户默认行为和完成条件 | Agent/客户 | 文件契约 | 不引入外部 Skill 调用 |
| 新客户行为测试 | 测试 | 输入、生成、公开入口、失败与续接 | unittest | 标准库及业务公开接口 | 分能力组织，避免单文件膨胀 |

## Failure Boundary Map

| 步骤 | 前置 | 影响的消费者 | 失败及恢复 |
| --- | --- | --- | --- |
| 输入/输出边界 | 客户文件、任务身份 | 全流程 | 非法输入在效果前拒绝 |
| 模型与镜像准备 | 明确来源、对应授权 | 迁移 | 分支失败；数据准备仍执行；复用已固定资产 |
| 各数据准备 | 数据来源与原生配置 | 对应评测 | 逐项记录；其他数据及部署继续 |
| 迁移/适配/复核 | 准备完成 | 精度 | 缺实际资料返回 Agent 下一步，保留容器与回执 |
| 各精度项 | 对应数据、有效服务 | 完整交付 | 保留其他有效项；不重复部署 |
| 三文件生成/导出 | 全部必需基线有效 | 对外交付 | 仅续接导出，不发布部分文件 |
| 证据不可写、取消 | 全局 | 所有新效果 | 停止后续操作，保留资源状态与部分产物 |

## 实现台账

| 步骤 | 依赖 | 状态 | 验证 |
| --- | --- | --- | --- |
| 输入与默认值 | 批准的模板 | 已实现，离线通过 | 严格哨兵、字段诊断、冲突与 prompt 补齐、公开 CLI |
| 准备与部署生成 | 输入 | 已实现，离线通过 | 本地优先、供应商顺序、冻结环境、镜像复用、容量和请求；真实安装及设备待验收 |
| 精度配置与三文件 | 有效证据 | 已实现，离线通过 | 子集/前 N 条、数据摘要及 token 长度、实际配置生成、联合校验和原子发布 |
| 续接和失败隔离 | 各阶段接口 | 已实现，定向离线通过 | 独立数据准备、有效评测项复用、语义待复核不重启、证据变化拒绝；真实取消/超时专项未运行 |
| 文档、兼容、结构审查 | 实现 | 已同步，离线通过 | 原回归、18 个公开帮助入口、独立副本计划、业务/治理链接及结构审计 |
| 真实 Ascend 验收 | 授权目标环境 | 未运行 | 本地离线结果不代替设备验收 |

改造从本地 dev 的 `e7542ca` 开发，提交为 `c68632517705016f9d960076b30134c7ae66ffa0`，以 `--no-ff` 合并提交 `d8e30423b72bd9e7266f036b3b3d7a32522a1f3f` 合入本地 dev。用户授权的原主题分支删除已完成；未推送。实现提交与合并提交内容树一致；main、origin/main 及当次查询的远端 main 均为 `e0955bda6e2fd308b194297a5635f73870fc5d09`。

## 验证证据

2026-10-28，以下为客户改造提交前的验证快照。完整命令保留在 [TESTING](TESTING.md)，本次结果：

| 命令或检查 | 结果 | 边界 |
| --- | --- | --- |
| `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py'` | 76 项通过，无跳过 | 原 44 项加 32 项客户行为/兼容回归；外部 SDK、Docker 效果使用模拟 |
| TESTING 的 AST / JSON / `bash -n` 检查 | 208 / 42 / 2 个业务文件通过 | 不加载模型或 SDK |
| TESTING 公开帮助枚举 | 18 个通过 | 原目录和独立复制副本由回归共同验证 |
| 独立副本计划 | 12 主入口、5 辅助入口、客户 --task 均通过 | 清除 PYTHONPATH，不创建运行目录，不下载、不安装；resolve.py 仅提供只读发现 |
| TESTING 文档检查 | 71 份 Markdown 链接、锚点、围栏及策略一致性通过 | 同时覆盖业务 Skill 和开发技能 |
| `source_shape_audit.py --repo-root . --policy .codex/code-structure-policy.toml --baseline .codex/code-structure-baseline.json` | 0 errors、161 warnings | 226 文件、39 直接目录、1317 函数；策略和硬阈值未变 |
| `git diff --check` | 通过 | 仅差异格式检查 |
| 资源枚举与复制 | 12 个 Skill、293 个静态资源，当前集合和摘要一致 | 不依赖构建清单，新增辅助 CLI 随完整总 Skill 复制 |

三文件回归使用合成准备、部署及精度证据；生成器、原始证据校验、平铺发布和重复导出是真实代码。输出 ready 仅证明该合成证据链通过，不证明客户 prompt 的真实 Ascend 全流程已经运行。

## 结构审查

职责保持总入口、阶段、任务三层。新客户编排独立于旧协调器；准备拥有环境及资产操作，verify-agent 唯一拥有服务操作，精度只管理自有评测进程；跨 Skill 走固定公开 CLI 与文件契约。新增共享代码仅是总入口拥有的静态本机地址及官方模型缓存路径策略。

写入基线前的普通审计仅有 21 项 `baseline_tighten_required`，均为指标改善后需收紧，无新增硬超限或基线退化。使用规定的 `--write-baseline` 纳入新文件、收紧已有改善；未使用 `--approve-baseline-increase`，未修改策略。相对改造前软提示总数由 143 增至 161，保留理由：

- 客户编排目录 11 个直接源码、交付目录 12 个、原生 EvalScope 目录 11 个：各自拥有明确输入、恢复、请求生成、发布或原生发现职责，没有新建同义入口或通用杂物包；下次增长应优先按能力子目录划分。
- `coordination.cli.main`、客户 `invoke.execute`、`runner.migrate/run/workflow` 的复杂度 11—15：分别只负责模式分发、回执检查及工作流边界，资产、部署、精度配置已提取；不继续向这些函数加入供应商或工具分支。
- 原生目录绑定、容量选择/校验、安装包身份、环境版本及镜像选择的复杂度 12—15：保留不同错误原因和身份检查，不能为降低指标合并失败类别。
- 准备辅助 CLI 的复杂度/分支各 13；模型及数据请求规范化复杂度 13/15：保留取消收敛及字段的互斥约束。没有新增超过硬阈值的函数；既有热点未扩容。各函数完整指标由审计生成，不人工维护副本。

## 审查结果与剩余验证

已修复的可复现问题包括：文本官方证据被误当 JSON、续接错误读取部署而非验证回执、语义待复核时可能重复部署、全量子集未绑定原生发现、数据变化仍沿用旧 token 长度、可变仓库在首次选择后变化、安装失败续接重新选 latest，以及 Docker context 优先级可能绕过本机限制。新增定向回归先复现失败，再验证修复。部署模型命名空间、容器身份变化、缓存链接边界和联合发布失败也有回归保护。

本次源码审查未留下已知阻塞缺陷；以下仍需授权目标环境验收，不能标为通过：

- 最新稳定 EvalScope、ModelScope/Hugging Face SDK 的真实安装及 API 组合；官方镜像平台 digest、硬件/版本配方和安装包源码采集。
- 客户模型真实权重、所有指定数据集原生适配、注册/源码适配、NPU 显存与 KV 预算、端口竞争和服务实际启动。
- prompt → Agent 观察 → 阶段执行 → 语义复核 → 多数据集精度 → 三文件的完整实机链路及取消/超时专项。
- 只支持当前原生标准文本映射边界；不支持的 adapter、数据格式或评分能力明确阻塞，不自行转换评分标准。AISBench 保留精度执行，三文件交付明确不支持。

## 知识与工作区状态

以下记录 2026-10-29 文档提交前已核对的事实；后续提交、合入和分支清理状态以 Git 记录为准。本次知识收尾修正评测来源文档中无条件升级的旧指令，区分客户复用策略与直接精度请求的 latest 规则，并将重复的旧指标收敛到 TESTING。新文档修订位于 `codex/evalscope-knowledge-closeout`；此前实现的提交、合入与分支删除已完成，本次新增修订仍遵循独立 Git 授权边界。

| 事实面 | 状态 | 说明 |
| --- | --- | --- |
| 代码 | verified-current | d2fd304 经 5fe40b1 合入本地 dev，内容树相同；本次收尾不修改源码、测试、模板或结构基线 |
| 运行态 | not-applicable | 本次无部署目标；真实 SDK、服务器发现、模型/NPU 和完整流程验收仍为 pending，未连接或运行 |
| 文档 | changed-and-verified | 合入版本、环境策略、当前验证与历史快照分清；业务文档摘要随内容变化，资源集合保持不变 |
| 规则 | verified-current | 项目 AGENTS.md 为当前规则入口，无局部 override 或重复 CLAUDE 规则；Git、环境及清场授权边界保留，不增设规则 |
| 记忆 | out-of-scope | Codex 生成记忆只读；本次未检索历史记忆或新增记忆 |
| 工作区 | verified-current | 开始收尾时 dev 干净，只有一个 worktree，原实现分支已删除；仅见两个已忽略的 Finder 元数据文件并保留。本次文档分支有独立新修订，保留待交付；无待删除业务资产 |

客户模板位于 [template.json](../skills/vllm-model-migration/assets/template.json)，字段说明见[客户合同](../skills/vllm-model-migration/references/customer-task.md)。交付目录只含三个 JSON；不描述或调用外部性能调优 Skills。旧 --request、独立任务、历史 analyze 和旧导出布局继续保留。


## 2026-11-01 EvalScope 共用环境安装

本次按已批准计划从本地 dev 基线建立功能分支开发。实现阶段仅修改本仓，不修改性能插件调用方。客户 task 三组字段、类型、必填和整数 -1 语义、三个交付 JSON 均不变。新接口和来源摘要唯一维护在[安装契约](../skills/vllm-model-migration/references/evalscope-installation.md)。此前环境复用章节记录历史实现，其自动发现、条件安装和 selection 语义已被替代。

### Change Shape Map

路径相对完整业务 Skill；测试及维护文档仍在仓库层。修改前结构审计为 0 errors / 160 warnings；以下为编码前确定的责任映射。

| 路径 | 所有者与改动原因 | 消费者/公开性 | 允许依赖 | 预期规模与放置理由 |
| --- | --- | --- | --- | --- |
| scripts/install_evalscope.py | 原版安装工具，字节不变 | 准备操作/公开 CLI | 标准库 | 150 行，同总 Skill 提供完整搬迁资源 |
| scripts/evalscope_installation.py | 参数与路径边界契约 | context、准备 CLI/共享公开模块 | 标准库、skill_paths | 小于 100 行，两处真实消费者共享同一契约 |
| 准备 scripts/customer_assets/evaluator.py | 安装调用、结果适配、性能环境信息 | 准备 CLI/私有 | 日志进程边界、共享契约 | 原 152 行，替换旧发现/选择职责后缩小 |
| 准备 scripts/customer_assets/discovery.py | 删除候选发现 | 原私有 | 无 | 原 42 行，不再需要 |
| 精度 verify-agent-plus/scripts/inspect_environment.py | 删除旧公共探测入口 | 原公开 | 无 | 原 102 行，原生精度执行检查仍保留 |
| scripts/customer_workflow/context.py | 内部安装配置校验 | runner/私有 | 共享契约、来源引用 | 原 29 行，预期小于 100 行 |
| scripts/customer_workflow/effects.py | 请求透传、预览、环境绑定 | runner/私有 | 公开 CLI、内部台账 | 原 62 行，预期约 100 行 |
| scripts/customer_workflow/state.py | 续接绑定与旧回执拒绝 | runner/私有 | 来源引用、证据 | 原 78 行，预期约 100 行 |
| scripts/customer_workflow/runner.py | 透传选项、预览提前返回、阻止失败环境的精度 | 客户 CLI/私有编排 | 上述私有编排模块 | 原 255 行；run 复杂度 15 不增加 |
| 准备 scripts/customer_assets/cli.py | 新请求、预览回执 | 客户编排/公开 CLI | 安装适配模块 | 原 79 行，预期约 90 行 |
| 准备 scripts/customer_assets/environment.py | 保留 AISBench/SDK，移除旧 EvalScope 路径 | 准备 CLI/私有 | 标准库进程和网络 | 原 142 行，不扩展依赖管理职责 |

没有引入新包、服务、调度器或锁。共享契约只向下依赖静态路径能力，不跨 Skill 导入私有实现。新增文件纳入资源规则和独立搬迁检查。

实际结构：context 29→57 行、effects 62→85、runner 255→257、state 78→93、准备 CLI 79→84、安装适配 152→79；原版安装工具 150 行，共享契约 53 行。run 复杂度保持 15、语句 32→31；workflow 11→13，只增加预览分流与精度的环境就绪检查，环境效果仍归独立模块。prepare CLI 的复杂度 10→12、分支 10→13，保留参数校验与回执分流职责；normalize 复杂度 12 来自六字段类型校验。以上软提示均低于硬阈值，未借结构拆分改变执行语义；其他历史软提示不在本次扩大重构。审计先确认全部 136 个错误仅为删除/改善项要求收紧，再使用标准 --write-baseline 纳入新增项并收紧，未使用上调批准参数。


### 失败边界与实现

- 无效内部配置、资产/证据路径重叠与旧回执在业务效果前拒绝；help/dry_run 返回 not_run，迁移 plan 不调用安装脚本。
- 正式安装失败、非零退出、异常结果或超时只阻塞环境及其消费者；独立资产继续。日志、argv、退出码和局部安装保留，不自动重试/清理/换环境。
- 首次成功绑定实际 Python 与 EvalScope 版本；续接每次仍调用脚本。变化使绑定永久失效，要求新 run-id，不复用旧精度结果。
- 真实安装结果保留 Python 启动路径；versions.evalscope 与精度请求一致。内部 performance_handoff 的 benchmark_python 与该路径相同，PATH 首项为 CLI 目录且实际解析结果已核对。性能插件仍须实际采用并复核该环境。
- 取消继续使用既有自有进程组停止边界；共享台账写入失败不能伪装业务成功。没有新增跨插件互斥能力，串行使用为执行约定。

### 验证与审查边界

先添加新行为测试，9 项因缺少安装副本/参数契约/适配接口及旧编排行为失败；完成实现后相应测试通过。删除仅测试旧候选/探测/selection 行为的用例，保留 AISBench、资产 SDK、日志和独立失败继续回归，增加真实公开入口在搬迁副本中的帮助/安装预览与续接测试。当前命令和结果统一见[验证基准](TESTING.md#当前验证基准)。 审查另复现并修正 pip 日志末行无换行导致末尾 JSON 漏解析的问题，补充对应回归；精度请求与性能交接的同环境链路也有离线测试。复查无未解决的阻塞或可执行缺陷。

安装副本 SHA256 与来源一致；测试用的是临时文件和进程替身，真实原脚本仅执行 help/dry-run。没有真实 pip、模型/数据下载、Docker、NPU、精度或性能测试，不能报告实际共用成功。三个交付模板和客户模板未改动。

代码/契约为 changed-and-verified；真实运行验收为 pending；长期记忆为 out-of-scope。本节记录实现与提交前验证，后续 Git 操作以提交历史及交付回执为准，不改记为新增运行验收。临时验证证据位于仓库外。


## 2026-11-01 客户默认值兼容与自动补全

本轮从本地 dev 787dcf897a29cdd25e2a902b20bb3599032e6d35 创建 codex/customer-input-defaults，仅修改当前仓库。客户三组对象及字段集合、完整模板、原版安装脚本和三个交付 JSON 保持原接口；组内按字段接收缺失/null/整数 -1，旧字段仍拒绝。实际行为以[客户契约](../skills/vllm-model-migration/references/customer-task.md)为准。本节是工作区实现记录，不代表提交、合入、同步或实机验收。

### 修改形状与职责

路径均相对总 Skill 的 scripts/customer_workflow，测试相对仓库根。

| 路径 | 所有者与单一职责 | 消费者/边界 | 允许依赖与增长 | 放置原因 |
| --- | --- | --- | --- | --- |
| contract.py、intent.py、cli.py | 静态输入、明确 prompt 合成及无效果计划 | 客户公开 CLI | 纯策略与标准库；contract 115→132 行 | 保留既有输入入口，不把外部发现放入校验 |
| context.py | 实际后端及资产边界前置检查 | 客户 runner；内部 | 原证据/请求模块；57→73 行 | 在写台账前使用合成输入 |
| defaults/policy.py | 静态默认判定、容器名称和待解析字段 | contract/intent/runner；内部 | 标准库；新增 | 多个消费者共享同一默认语义 |
| defaults/model.py | 根据绑定来源和实际 config 解析名称 | runner；内部 | 当前模型身份门禁、证据和受控模型路径；新增 | 不复制模型获取或身份复核能力 |
| defaults/devices.py | 完整主机候选池证据及与容量观察对应 | runner；内部 | 原 effects 公开 observe、字段契约；新增 | 不在总入口重写 NPU 表格解析或容量规划 |
| defaults/state.py | 声明与动态值分离、逐项冻结及失效 | runner/model/devices；内部 | 原证据及 Session；新增 | 单独拥有续接约束 |
| runner.py、state.py | 委托新阶段并保留冻结数据 | 客户流程；内部 | defaults、原阶段公共 CLI；runner 257→260 行 | 保留三层职责、资产独立性和历史回执 |
| compile/assets.py | 原生 catalog 的模型名就绪前提 | 精度请求编译；内部 | 原证据模块；新增一个消费者守卫 | 缺名称只阻塞真实消费者 |
| tests/vllm-model-migration/test_customer_defaults.py、test_default_resolution.py | 默认契约与证据/续接回归 | 标准库 unittest | 临时合成文件、公开入口与效果替身 | 按能力区分纯输入和动态发现 |
| 既有契约、模型身份、CLI 回归 | 新默认策略及完整搬迁 | unittest | 保留显式输入和旧字段拒绝覆盖 | 旧断言只按已批准的新契约调整 |

原 customer_workflow 直接源码文件仍为 12；新增 defaults 子包 5 个文件，内部依赖单向，未导入兄弟 Skill 私有模块。Session 增加可选仅关键字参数 effective_task，将原始声明快照与实际资产路径保护分离；原位置参数调用保持兼容，其他既有函数参数未扩展，新函数最多 4 个参数。run 复杂度 15→13、分支 11→10、语句 31→29；context.start 复杂度保持 3、分支 2→1、语句 7→6。workflow 复杂度 13→14，保留为委托编排；native_catalog 复杂度 13→14，新增名称前提与原有来源校验同属消费者边界。两者仍低于硬阈值；保留原目录 12 文件的软提示以避免无关搬迁。新增默认解析函数不超过复杂度软阈值。

### 失败边界与连续性

| 步骤 | 前置与输出 | 失败影响 | 保留证据 |
| --- | --- | --- | --- |
| 输入/prompt 合成 | 三组对象、合法显式值；产生声明 | 无效输入/明确冲突在外部效果前结束 | 原 task 摘要、具体字段诊断 |
| 模型名默认 | 当前模型解析、身份门禁及实际配置 | 阻塞名称消费者；模型/镜像准备和独立数据工作继续 | 来源快照、config 摘要、资产身份、default_model 诊断 |
| 设备默认 | 本机公开 observe、Agent 完整清单及容量观察 | 阻塞部署；其他准备继续 | observe 回执、完整 ID、主机身份、default_devices 诊断 |
| 默认冻结 | 已合成声明、上次解析绑定 | 已冻结值或身份变化阻塞对应分支；不扩大候选池、不复用不匹配结果 | 声明、effective_inputs、default_resolutions、失效标记 |
| 取消/证据不可写 | 既有共享状态/取消处理 | 沿用任务停止边界 | 已有部分回执与资源状态 |

静态计划只补静态默认并列出未解析项。原始 task 不回写；无 context 的中间续接仍复制声明、实际值和动态绑定，不能借丢失 context 清除冻结。安装参数及其版本绑定策略不变；只将环境前置检查和续接识别改为使用实际后端。

### 验证与审查

先新增测试得到 RED：8 项静态用例中 1 failure/6 errors，10 项动态用例中 1 failure/9 errors，均对应未实现的默认语义和消费者边界。随后补充实际后端续接、prompt 路径先于台账保护，无 context 续接保持冻结，以及 prompt 改选数据集后不再保护未使用默认目录的反例，分别复现后修正。没有通过修改测试来取消既有身份、设备范围或原生精度门禁。

全量命令为 `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -v`，150 项通过且无跳过。包括完整目录中文/空格搬迁、全默认 plan 和安装帮助预览、实际源文件摘要与配置一致性、设备允许池而非固定 TP、显式数据选择不追加、全量 limit 和原生交付旧回归。静态检查 217 Python/42 JSON/2 shell、19 个帮助入口、73 份文档检查和 git diff --check 通过。结构审计先确认只有 15 项改善/删除要求收紧，再用标准 write-baseline 更新，最终 0 errors/163 warnings；无策略阈值调整、无硬热点上调。

审查重点为默认值不能绕过模型身份、实际设备观察或旧结果绑定。审查补充部署前主机核验：默认池绑定的 Docker 主机身份须与实际公开 host_observation 一致，测试确认不一致时不会进入容器替换或迁移执行。NPU 原始表的完整解释仍由 Agent 依据实际公开观察完成，脚本检查清单类型、来源摘要、主机身份及容量清单集合。静态记录不是硬件事实验证；真实安装、模型/数据下载、Docker/NPU 和精度/性能均未执行。知识状态：代码与文档 changed-and-verified，规则 verified-current，实机验收 pending，记忆及跨仓同步 out-of-scope，工作分支保留未提交。

## 2026-11-01 prompt 合成顺序收口

继续当前默认值工作分支，按已批准方案收口输入处理；不新增客户字段、不改变三个交付 JSON、不修改安装脚本及升级策略。原工作区差异保持，未授权提交、合并或跨仓同步。

### 修改形状与失败边界

路径相对总 Skill scripts/customer_workflow；以下为修改前记录。

| 路径 | 所有者与单一职责 | 消费者/边界 | 允许依赖与预期增长 | 放置原因 |
| --- | --- | --- | --- | --- |
| contract.py | 分离显式输入校验与静态默认 | cli/context/intent；内部 | 标准库/defaults.policy；132 行，预计增加约 25 行 | 既有字段规则唯一归属，不引入另一套 Schema |
| intent.py | prompt 字段校验与唯一补值 | context；内部 | contract；45 行，预计增加约 15 行 | 保留两层路径及冲突规则 |
| context.py | 合成一次，完成效果前边界检查 | runner；内部 | intent/contract/证据及 Session；73 行，预计增加约 10 行 | 最终参数的组装入口 |
| cli.py | 原始校验、无效果计划与执行分流 | migrate.py；公开入口适配 | contract/context runner；42 行，预计不增加职责 | 不在执行分支提前生成默认 |
| runner.py | 消费 context 的合成结果 | 客户业务流程；内部 | 现有编排及效果模块；260 行，预计缩减 | 删除重复合成与重复路径守卫 |
| tests/vllm-model-migration/test_prompt_resolution.py | 入口顺序和共享参数回归 | unittest；仓库测试 | 临时输入和效果替身；新增 | 测试入口行为而非只调用纯补值函数 |
| 既有默认测试与文档 | 私有接口迁移和行为说明 | unittest/维护者 | 当前契约；少量修改 | 保持快照、续接与计划边界 |

修改前台账列为规则/审计、入口 RED、实现、独立验证、审查和知识收尾六步；各项实际结果见下节。每项检查独立运行，失败只阻塞依赖该结果的结论。指标由仓库 source_shape_audit 的修改前后 JSON 记录，不上调硬热点。

| 边界 | 前置/输出 | 失败影响与诊断 |
| --- | --- | --- |
| 原始输入与 context 形状 | 原 task/证据身份；合法显式值 | invalid_input；不合成参数或执行业务，不允许 prompt 掩盖非法 JSON |
| prompt 合成 | 合法两层字段、无明确冲突；最终声明 | 显式冲突沿用 owner=user/blocked；不安装、不部署、不执行评测 |
| 最终后端和路径检查 | 同一合成声明；允许写入及环境边界 | invalid_input，必须早于创建执行台账；AISBench 不进入 EvalScope 检查 |
| 快照和续接 | 原 inputs 保持原归一化语义；声明/实际值分别冻结 | 输入或冻结值变化沿用原续接拒绝；不得用默认重选恢复不匹配结果 |
| 后续业务 | 已校验的同一合成结果 | 保留独立准备、取消和证据不可写边界，不改变现有失败隔离 |

### 实现、验证与审查

contract.validate 只校验显式值并保留未指定标记，normalize 在该结果上应用原静态规则；cli 执行分支不提前生成默认。context 完成一次 prompt 合成，将同一实际 task 交给路径/后端前置检查和 runner。原 inputs 单独按既有 normalize 规则生成，仅用于快照和续接比较，不反向参与执行选择。私有 context.start 改为接收原始 JSON，并返回 context、Session、合成 task 和冲突列表；既有三个调用测试同步迁移，未新增客户接口。

新增 test_prompt_resolution.py 的 12 项入口回归只替换业务执行效果，保留真实读取、校验、prompt 合成、路径保护、台账和续接。第一轮 11 项得到 8 failures/2 errors（含子用例），确认重复合成、前置生成默认、非法 prompt 分类和计划诊断问题；实现后全部通过。审查再补一个来源变化用例，先复现旧参数绑定新 task 摘要，再增加台账创建前的摘要复核，得到 GREEN。输入冻结不能因减少重复读取而削弱。

最终 `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -v` 通过 162 项，无跳过。包括原始和完整搬迁资源、公开 CLI、默认参数、路径边界、AISBench/EvalScope 分支和续接；新测试验证前置检查与执行使用同一对象、容器名和模型名的区别、非法值不能被另一来源修补、--plan 不读取 context 且不创建运行目录。静态检查 217 Python/42 JSON/2 shell；19 个公开帮助入口；12 个开发技能/37 个快照资源及依赖闭包均通过。客户模板和原版安装脚本与 HEAD 字节一致。

结构审计确认全部 19 项 error 都为已有指标改善后要求收紧基线，再通过标准 write-baseline 纳入新测试并收紧指标；最终 0 errors/163 warnings，基线 241 个文件，未修改策略或上调硬热点。文件行数 contract 132→152、context 73→85、intent 45→59、cli 42→43、runner 260→254。run 复杂度 13→11、分支 10→9、语句 29→23；preflight 复杂度 6→4、分支 3→2、语句 12→7。start 复杂度 3→5、分支 1→3、语句 6→13，增长来自组装及来源复核，低于软阈值。新函数复杂度最多 8、参数最多 2；既有 start/preflight/run 参数数未增加，environment 由两个参数减少为一个。

软提示审查：customer_workflow 保持 12 个直接源码文件，不新增生产模块；run 保留复杂度 11 的提示，因为剩余分支分别处理无 context、冲突、续接、取消和证据失败，同属编排责任。未改动的 workflow、migrate、native_catalog 等提示沿用上一轮审查理由，不作无关拆分。现行架构和客户/观察契约同步说明顺序、快照用途和 JSON-only 计划；治理文档及 git diff --check 作为最终交付门禁执行。

规则/修改前审计、RED-GREEN、实现和独立离线验证 succeeded；审查未发现剩余可行动缺陷。知识状态：代码与文档 changed-and-verified，规则 verified-current，实机验收 pending，长期记忆及目标仓 out-of-scope。工作区及主题分支保留，未提交、合并、同步或清理；未安装真实依赖、下载模型/数据或操作 Docker/NPU。

## 2026-11-01 EvalScope 续接继承与安装前核验

当前主题分支继续实现已批准的续接方案；保留前两轮工作区改动。安装选择继承、实际版本固定、开关仅本次有效，正式安装前核验已有环境；不修改原版安装脚本、客户字段或交付 JSON，不操作真实共享环境。

### 修改前职责与失败边界

| 路径（相对总 Skill scripts） | 所有者/职责 | 消费者与依赖 | 预计增长/放置理由 |
| --- | --- | --- | --- |
| customer_workflow/evaluation/history.py | 安装选择证据与旧回执恢复 | selection/lifecycle；现有 sources/内部契约 | 新增；独立的证据边界，不猜测 Python 来源 |
| customer_workflow/evaluation/selection.py | 合并环境选择与本次开关 | context/测试；历史证据与安装契约 | 新增；只生成内部执行计划，不调用安装 |
| customer_workflow/evaluation/probe.py | 30 秒只读环境核验及日志 | lifecycle；标准库与现有 evidence | 新增；唯一探测效果边界，无 pip/设备操作 |
| customer_workflow/evaluation/lifecycle.py、__init__.py | 正式安装、预览隔离、绑定 | effects；selection/probe、注入既有公开 operation | 新增；不在既有编排热点叠加规则 |
| customer_workflow/context.py、state.py | 先验证续接身份再检查继承路径；保留选择 | runner；既有证据、evaluation | context 85 行/state 96 行；预计小幅调整，保持输入快照 |
| customer_workflow/effects.py、runner.py | 委托环境模块、传递同一安装计划 | 原公开准备 CLI；evaluation | effects 85 行/runner 254 行；预计缩减/小幅调整，不更改资产独立性 |
| evalscope_installation.py | 分离原始参数类型校验与默认归一化 | 客户与准备公开 CLI | 原 53 行；不改参数集合或脚本副本 |
| tests/vllm-model-migration/evaluation/ | 安装协议与续接回归 | 标准库、合成回执和效果替身 | 新增续接测试，相关原安装测试一起归组；原测试根新增后将达 17 文件、超过 16 硬限制，按同一环境能力归类，不上调阈值 |
| 现有测试及现役文档 | 私有接口迁移、契约和验证记录 | 当前公开入口/资源 | 小幅调整；不扩大客户接口 |

修改前台账依次安排规则/结构核对、继承与前检 RED、实现、全量/入口/搬迁/资源/文档/结构验证、审查和知识收尾；实际完成结果见下节。审计保存修改前函数/文件指标，硬基线不得上调。新增 evaluation 子包不增加 customer_workflow 直接源码文件数。

| 失败边界 | 处理与消费者 |
| --- | --- |
| 输入类型/路径越界/共享证据损坏 | 在效果前终止，不构造虚假选择 |
| 配置变化/无法可靠恢复旧选择 | 环境 blocked、新任务诊断；零安装调用，原绑定不因请求变化失效 |
| 确认绑定环境消失或身份变化 | 保留证据并永久 invalidated；零安装调用，精度/交付阻塞 |
| 探测超时/权限/异常输出 | 环境 blocked，保留原绑定，不推断变化、不重试 |
| 安装失败/返回不符 | 保留请求/日志/选择；独立资产继续，绑定变化使旧结果失效 |
| help/dry_run | 独立预览回执、not_run；不覆盖成功环境，不继续业务 |
| 取消/证据不可写 | 沿用任务致命停止边界，保留已完成证据 |

### 实现、验证与审查

context 在创建台账前读取并核验续接身份/请求/回执，history 恢复可靠选择，selection 一次合成安装计划后交给路径检查和执行。新增 evalscope_install_selection 保存基础 Python、规范化 venv、原版本要求及请求摘要引用；首次正式安装前持久化，失败和无 context 中间续接都不丢失。实际成功版本从 evalscope_environment 继承为本次精确 version，upgrade/help/dry_run 不从历史请求复制。基础 Python 只解析明确命令，不把评测 Python 反推为基础解释器。

lifecycle 区分预览与正式准备。配置差异在效果前报告新旧值并阻塞环境，原绑定保持；probe 用原启动路径做 30 秒只读核验，确认漂移或消失才使任务永久失效，未知失败保留绑定。前检通过仍调用原脚本，安装后继续核验；安装失败后补一次只读核验，避免部分安装改变版本却未失效。environment_preview 独立保存，不覆盖成功回执，不建立选择/绑定，不进入资产/部署/精度/交付；无法恢复历史选择也不能丢掉预览意图而进入业务。共享证据损坏和不可写保持致命边界，环境局部失败不吞掉独立资产准备。

先补 16 项续接行为回归得到 RED（16 errors，缺少新能力及接口），实现后全部 GREEN。审查再补两项反例，分别复现安装失败留下变化环境和继承 venv 被符号链接重定向未失效，再修正。第二轮补三项回归，其中损坏历史下的两种预览及不可用新基础 Python 的新任务诊断得到 3 failures；一致旧回执反例同时通过。修正后共 21 项新增测试通过。旧测试只迁移私有接口、局部阻塞边界和预览步骤名；原安装/精度/性能映射与公开 CLI 断言保留。

| 验收项 | 命令或证据 | 结果 |
| --- | --- | --- |
| 续接继承、前检、预览与旧回执 | `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_evalscope_resume.py' -v` | 21 项通过 |
| 全量、公开入口与完整 Skill 搬迁 | `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -v` | 183 项通过，无跳过；仅临时合成数据与外部效果替身 |
| 语法、入口、开发资源与文档 | [验证文档](TESTING.md)对应独立命令块 | 222 Python/42 JSON/2 shell；19 个 help；12 技能/37 资源；73 文档通过 |
| 原版脚本与客户输入边界 | 字节摘要回归及与 HEAD 原始字节比较 | install_evalscope.py 与 template.json 保持不变；三个交付 Schema 未改 |
| 结构 | 仓库 source_shape_audit.py 普通审计、标准 write-baseline 后复核 | 0 errors/165 warnings；248 文件、42 目录、1519 函数 |
| 差异 | `git diff --check`，新文件语法/尾随空白检查 | 通过；未提交文件也纳入验证 |

结构审计先确认 90 项 error 全为改善/删除指标要求收紧，再用标准 write-baseline 纳入新资源；未调整策略或上调硬热点。context 85→91 行、state 96→87、effects 85→71、runner 254→252、安装参数契约 53→57。新生产子包 5 文件，最长 history 83 行；context.start 复杂度 5→6、分支 3→5、语句 13→16。测试根直接文件从 16 降至 15，新增 evaluation 子包收纳原安装测试与新续接测试，避免新增后达到 17 而超过硬上限。

软提示保留理由：selection.resolve 复杂度 12，分支分别处理可靠历史、版本绑定、路径继承、明确解释器和失效状态，同属一次安装选择合成；probe.check 复杂度 11，集中校验同一 JSON 的类型及三项身份。两者低于硬阈值；安装参数 validate 的复杂度 12 是原 normalize 类型校验迁移，未增加类型分支。没有为降低数字机械拆分连续校验；其余既有软提示沿用先前审查。新生产函数最多 4 个参数，既有私有 context/preflight/installation/workflow 增加历史或计划参数以传递同一结果，公开 CLI 未增加选项。

规则/修改前审计、RED-GREEN、实现、独立验证与审查 succeeded；审查修正后的范围内未发现剩余可行动缺陷。代码和文档 changed-and-verified，现行规则 verified-current；实机安装、续接和两端共用验收 pending，长期记忆及目标仓 out-of-scope。保留已有未提交改动和主题分支，没有 commit、merge、push、同步或清理；未安装真实依赖、修改共享 venv、下载真实模型/数据或操作 Docker/NPU。

## 2026-11-01 数据集 prompt 逐字段补值

仅实现已批准的列表内部补值：名称序列完全一致才逐字段合成 path/subset_list/limit；不新增索引路径，不改变客户字段、交付 JSON 或 EvalScope 环境策略。保留当前分支前三轮未提交改动，不提交、合并、同步或操作真实环境。

### 修改前职责与失败边界

| 路径（相对总 Skill scripts；测试为仓库相对路径） | 所有者/单一变更原因 | 消费者、边界及允许依赖 | 预计增长与依据 |
| --- | --- | --- | --- |
| customer_workflow/contract.py | 分离数据集字段校验与完整条目必需条件 | 客户与 prompt 共享显式类型校验；纯输入 | 152 行，预计增加约 20 行；复用规则，不放宽原始 JSON |
| customer_workflow/intent.py | 同一合成逻辑返回结果及精确冲突 | context 与现有私有调用者；只依赖 contract/defaults | 59 行，预计增加约 50 行；属于已有 prompt 职责，不新增同级模块 |
| customer_workflow/context.py | 消费一次 prompt 合成的结果和冲突 | runner；保持路径与环境边界 | 91 行，预计基本不变；不改变安装行为 |
| tests/vllm-model-migration/test_dataset_prompt.py | 输入矩阵、真实入口及下游消费者回归 | unittest 与临时合成证据 | 新增约 300 行；原测试根 15 文件，增加后 16，不超过硬上限 |
| tests/vllm-model-migration/test_prompt_resolution.py | 观察统一合成入口而非旧私有接口 | 既有入口回归 | 小幅替换，保留合成次数与快照断言 |
| 客户/观察契约及现役维护文档 | 列表写法、默认规则和实际验证结果 | Agent 与维护者 | 小幅更新，不修改模板或安装契约 |

修改前审计 0 errors/165 warnings。accuracy 复杂度 10、分支 5、语句 14；dataset 为 6/4/11；parameters 为 8/4/12；conflicts 为 5/3/11；resolved 为 4/3/9；declaration 为 3/2/7。原函数参数数为 1 或 2，新增函数保持明确的数据输入，不新增模式开关或外部效果。

| 边界 | 前提与输出 | 失败及消费者处理 |
| --- | --- | --- |
| JSON/prompt 类型与条目结构 | 合法明确字段；prompt 不要求重复已有路径 | invalid_input，安装和业务调用为零；非法值不能由另一来源修补 |
| 名称序列和明确叶子冲突 | 完整名称序列一致、两边明确值一致 | blocked，集中报告数据集/字段；不执行任何业务补值 |
| 静态默认、路径检查及冻结 | 单一合成声明 | 沿用台账前路径保护、声明冻结；原始输入不回写 |
| catalog/准备/精度/交付 | 同一数据选择与实际证据 | 不改变现有分支失败隔离；旧 catalog 和不匹配结果拒绝复用 |

修改前台账安排规则/结构核对、行为 RED、实现、定向/全量/入口/搬迁/资源/文档/结构验证、审查与知识收尾；完成结果如下。各检查独立执行，失败不吞掉无依赖检查。

### 实现、验证与审查

contract.dataset_fields 与 dataset_list 共享字段类型和名称唯一性校验，dataset 单独保留客户完整条目的必需路径要求。prompt 的 name 必需，其他明确字段按相同类型规则校验；不提前把缺省补成路径或全量。intent.dataset_values 在名称序列一致后按叶子比较，prompt 未指定项不覆盖 JSON，子集列表不拆分。intent.compose 一次返回实际声明和全部冲突，context 直接使用该结果；原 conflicts/resolved 私有调用者复用同一规则。冲突诊断带字段和数据集名称，输入/快照/冻结及下游证据边界保持原合同。

新增 test_dataset_prompt.py 先运行 17 项得到 RED（14 failures/7 errors，含矩阵子用例），复现原整列表比较和自定义数据集部分 prompt 误拒绝。实现后，路径保护测试第一次仍失败：检查发现测试将数据放在运行台账的兄弟目录，实际不构成重叠；把测试改为台账父目录作为数据根后正确拒绝，没有放宽生产路径规则。最终 17 项全通过，原 12 项 prompt 顺序测试也通过；它们现在观察 compose 被调用一次，冲突结果由同一次合成返回。

消费者回归从 prompt 的 ceval limit=20、subset_list=[b] 出发，写入临时合成 JSONL 和 catalog，运行实际 dataset_jobs、数据 inspect_dataset、精度 task_config、交付 evaluation/accuracy.validate，验证样本选择、冻结配置和下游参数一致；旧 catalog 的 limit 不匹配时拒绝。未调用原生 SDK、服务或真实评测，不把合成基线当成测量有效的实机证据。完整三文件发布与搬迁由全量既有回归覆盖。

| 验收项 | 命令或证据 | 结果 |
| --- | --- | --- |
| 数据集矩阵、入口与消费者 | `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_dataset_prompt.py' -v` | 17 项通过 |
| 原 prompt 单次合成 | 同一 unittest 命令，`-p 'test_prompt_resolution.py'` | 12 项通过；原输入不回写、合成声明共享、计划无效果 |
| 全量与完整资源搬迁 | `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -v` | 200 项通过，无跳过 |
| 静态、入口、资源及文档 | [验证文档](TESTING.md)四个独立命令块 | 222 Python/42 JSON/2 shell；19 help；12 技能/37 资源；73 文档通过 |
| 结构 | source_shape_audit.py 普通审计、标准 write-baseline 后复核 | 0 errors/166 warnings；249 文件、42 目录、1543 函数 |
| 不变边界与差异 | 模板/原版脚本与 HEAD 字节比较、Schema 差异、新测试 AST/空白检查、`git diff --check` | 通过；未新增客户字段或交付 Schema |

结构审计的 12 项 error 全为指标改善后要求收紧；核对无硬违规后通过标准 write-baseline 更新，不改策略或上调硬热点。contract 152→165 行，intent 59→88，context 91→92；新增测试 268 行，生产同级目录不增长，测试根 15→16 文件。accuracy 复杂度 10→5，dataset 6→3；parameters 8→10，新增 compose 6、dataset_values 12。声明合成参数数保持 2，字段/列表校验最多 2；无新的外部效果或跨 Skill 私有依赖。

dataset_values 的复杂度 12 是本轮唯一新增软提示：名称序列检查和三字段双来源补值属于同一列表合成责任，19 条语句、8 个分支均低于软阈值，未为数字机械拆分。其余既有提示保持原审查范围。审查核对未新增数据集、未排序子集、未把非法值修成默认、未绕过续接冻结；未发现剩余可行动缺陷。

规则/修改前审计、RED-GREEN、实现、独立验证、审查与文档收尾 succeeded；现行代码和文档 changed-and-verified，规则 verified-current。真实数据/SDK/设备和评分验收 pending，长期记忆与目标仓 out-of-scope。既有未提交改动和当前主题分支保留，没有提交、合并、push、同步或清理；本轮也没有改变 EvalScope 安装选择、版本绑定或运行策略。

## 2026-11-01 EvalScope 性能环境交接核验

仅当前仓库实现已批准的性能启动前环境核验；保留前四轮未提交改动。原版安装脚本、客户 task.json、三个交付 JSON 和性能仓库不变；不新增依赖锁，不提交、合并或同步。

### 职责、修改前指标与失败边界

| 路径（相对总 Skill scripts；测试为仓库相对路径） | 语义所有者与单一职责 | 消费者/公开边界和允许依赖 | 增长及放置依据 |
| --- | --- | --- | --- |
| check_evalscope_handoff.py | 启动前核验公开入口 | Agent；只委托 evaluation.handoff_cli | 新增薄入口，保持总 Skill 搬迁后独立调用 |
| customer_workflow/evaluation/handoff.py | 请求、客户身份及安装证据一致性 | 私有；CLI 消费；同包 history、客户纯归一化与 coordination 来源 | 新增约 100 行，复用已有选择证据契约，不从目录猜环境 |
| customer_workflow/evaluation/handoff_probe.py | Python/CLI 只读探测及过程证据 | 私有；标准库、原安装脚本选项常量与 evidence | 新增约 120 行；外部效果单独归属，不调用安装或性能任务 |
| customer_workflow/evaluation/handoff_cli.py | 参数组装与独立尝试发布 | 私有；仅组装来源校验、路径边界、探测及终态 | 新增约 100 行；不进入客户编排或改写历史 |
| 准备阶段 customer_assets/evaluator.py | performance_handoff 补实际 venv | 现有准备公开 CLI 结果；不增加跨 Skill 私有导入 | 修改前 79 行；performance_handoff 复杂度/分支/语句 2/1/6，参数 1；仅增加结果字段 |
| skill_paths.py | 核验入口登记为必需资源 | 资源枚举和完整副本 | 修改前 227 行；只补必需文件，不改变枚举规则 |
| tests/vllm-model-migration/evaluation/test_evalscope_handoff.py | 历史证据、只读效果、搬迁及消费者行为 | unittest/合成文件/探测替身 | 新增约 400 行；归入已有 evaluation，测试根直接文件仍为 16 |
| tests/vllm-model-migration/evaluation/test_evalscope_installation.py | 新交接 venv 字段断言 | 现有安装回归 | 一项字段断言，保留原安装、精度和性能 Python 映射 |
| tests/vllm-model-migration/test_cli_compatibility.py、test_resource_discovery.py | 新入口帮助/计划/必需资源检查 | 现有公共入口及搬迁测试 | 小幅更新入口数量、计划请求和资源清单 |

修改前结构审计 0 errors/166 warnings、249 文件/42 目录/1543 函数；evaluation 直接源码 5 文件。准备函数和资源枚举职责不变；新增效果归于 evaluation，不增加 customer_workflow 根的职责或私有跨 Skill 依赖。

| 边界 | 前提、输出与依赖者 | 失败处理及保留证据 |
| --- | --- | --- |
| 请求形状、输出边界 | 唯一 customer_manifest 引用；包外运行目录 | invalid_input；不读引用的计划模式不建目录 |
| 客户/安装来源核对 | 同 task/run、快照、最终 EvalScope、成功正式回执、有效绑定 | blocked / evidence_invalid；仅阻塞本次交接，不修改客户任务或执行其他业务 |
| 只读环境探测 | 已核实来源和安全证据目录；输出 Python/venv/scripts/版本、CLI 解析和帮助 | 确认变化为 identity_changed，要求新任务；未知为 unverifiable，保留日志、不安装修复或重试 |
| 独立发布 | 探测通过且原引用未变化 | ready 只输出本次交接；取消或证据不可写为 failed，不产生可用环境结论 |
| 性能消费者 | Agent 获得本次 ready 并显式传入 Python/env | 本仓不执行性能；实际两端进程证据仍是共用验收前提 |

任务台账为：规则及修改前审计、失败回归、实现、定向与全量验证、资源/文档/结构检查、审查与交付。验证相互独立，单项失败不取消其他检查；实现中的失败仅阻塞本次性能环境交接。

### 实现与验证

新增请求只接受 customer_manifest 的 path/sha256；正式核对客户原 task 快照及最终后端、安装请求、成功结果和有效绑定。历史交接缺 venv 时从可靠安装结果恢复，既有字段仍须一致。性能 Python 始终采用精度返回的 Python，基础解释器只用于核对原安装选择。每次核验用当前调用进程 PATH 重建 CLI 优先路径，不复制安装时的 PATH 尾部。

Python 与 perf 帮助各有 30 秒超时、argv/stdout/stderr/退出状态；用独立尝试保存，不改写旧回执。保留 Python 符号链接启动路径，阻塞重定向 venv/CLI、缺文件和实际身份漂移。未知错误、取消和来源变化均不能产生 ready。Agent 指令要求用 argv 与子进程 env 传递结果并保存实际性能解释器/CLI；不新增目标性能插件代码或依赖锁。

先运行 12 项回归得到 RED（16 个子用例错误，缺少新模块），最小实现后通过。审查补充畸形历史和客户快照/最终后端反例，各复现一次未正确阻塞，再修正类型守卫和身份校验。最终新增 20 项回归，包含完整 Skill 搬迁后运行合成可执行文件、将返回环境交给独立合成消费者；没有安装 SDK、创建真实共享 venv 或执行性能负载。

| 验收项 | 命令/证据 | 结果 |
| --- | --- | --- |
| 新核验行为 | unittest discover 的 `-p test_evalscope_handoff.py` | 20 项通过，成功/漂移/未知错误/取消/写入失败/旧证据/计划/搬迁覆盖 |
| 全量及原流程 | `python3 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -v` | 220 项通过，无跳过 |
| 静态、入口、开发资源、文档 | [验证文档](TESTING.md)四个独立命令块 | 226 Python、42 JSON、2 shell；20 help；12 技能/37 资源；73 文档 |
| 原始字节边界 | installer/template 与 HEAD 比较、三个交付 Schema 差异 | 不变；安装脚本 SHA256 保持原契约值 |
| 结构与差异 | source_shape_audit.py，纳入新资源后普通审计；git diff --check | 0 errors/166 warnings；254 文件/42 目录/1584 函数；无阈值上调 |

evaluation 从 5 增至 8 个直接源码文件，仍属于同一环境能力；测试同组追加，不增加已达 16 的测试根文件数。新生产函数参数最多 5 个（过程日志记录器），复杂度均不超过 9；安装适配和资源枚举函数指标不变。现有 166 项软提示保留此前审查理由，本轮无新增软/硬热点；标准 write-baseline 仅纳入新文件与本次真实指标，不使用阈值豁免或上调批准。

代码与文档 changed-and-verified，规则 verified-current；源代码审查修正后无剩余可行动缺陷。真实安装、两端运行与共享环境验收 pending；性能仓库、全部依赖锁及长期记忆写入 out-of-scope。分支仍为 codex/customer-input-defaults，保留所有已有改动；无提交、合并、push、同步、清场或真实设备操作。


## 2026-11-02 功能输入模板与生成预算

客户功能请求曾统一使用128 tokens，且文本使用裸prompt、Chat/SSE未关闭可切换的thinking，实际Qwen3.5三项均以length结束。本次按获准方案将固定题目经原生模板生成，复用8192输出预算并把功能输入长度纳入容量，完整结束与语义复核要求保持。

| 路径 | 所有者/职责 | 消费与边界 |
| --- | --- | --- |
| verify/customer/functional_inputs.py | verify公开只读模型输入观察 | customer.py功能操作；已有Transformers/Jinja环境，不加载权重或安装 |
| customer_workflow/compile/generation.py | 客户生成策略、观察身份、容量与三项payload | 编译器与effects；标准库与固定文件证据 |
| customer_workflow/effects.py | 委托功能输入公开观察 | runner部署前；失败在分配设备和删除容器前阻塞 |
| compile/deployment.py、accuracy.py、runner.py | 消费统一预算与已观察输入 | 保留客户字段、部署及精度边界 |
| verification/request.py、protocol/usage.py、protocol/cases.py | 可选expected_prompt_tokens的形状与真实usage核验 | 旧请求兼容；新增客户用例要求长度精确相等 |
| tests/vllm-model-migration/functional/ | 模板、身份、预算、协议与公开计划回归 | SDK替身仅用于离线接口边界，真实模型另验 |

初始RED复现128导致完整回答被截断；同批测试证明新模板与预算路径缺失。服务器首次观察暴露模型准备清单不包含辅助元文件，新增通用回归后改为：先校验已准备非权重文件，再观察全部非权重文件及前后集合；没有按本次文件名加排除项。真实服务输入长度还须与观察完全相等，防止模板/分词版本差异被下界检查遗漏。

结构：deployment.py减少13行，runner.migrate复杂度11→7、分支8→5，刷新工具基线登记改善及新增文件，未调整阈值。generation.bind复杂度14、原生render复杂度12、case_shape复杂度12均低于硬阈值15；保留在同一职责内分别校验观察归属与数据形状、两种分词路径一致性、公开用例形状，未引入额外框架或隐藏副作用。原生SDK效果留在verify，公开委托留在effects，编译模块不启动子进程。

当前兼容边界：自动观察使用绑定evaluator Python的本地原生模板；显式覆盖tokenizer/聊天模板等配置时明确拒绝静默使用错误来源，需另行绑定对应有效来源。模板变量存在表示可尝试该参数，实际渲染失败或生成触顶仍如实失败。运行结果以本次独立证据为准，不据离线通过宣称端到端完成。
## 2026-11-03 评测数据交接修复（工作区实现）

本次只处理 C-Eval/GSM8K 的原生目录准备、路径绑定和推理前数据预检；不改部署、设备、服务端口或性能策略。原始数据与历史证据保留，运行资产在包外。

### 修改前职责与失败边界

| 路径（业务根内） | 所有者与单一职责 | 消费者/边界 | 依赖及预计增长 |
| --- | --- | --- | --- |
| dataset-prepare 的 request、prepare 与新增 dataset_prepare/native_layout.py | 准备任务构造并核验派生数据目录 | 公开 prepare CLI；内部布局模块私有 | 标准库、同包 assets；新增约 120 行 |
| verify-agent-plus 的 runtime/evalscope/data.py、samples、probe | 原生配置路径绑定与数据加载核验 | 同任务 catalog、preflight、run；私有 | 同包配置/样本，不导入其他 Skill；新增约 180 行 |
| verify-agent-plus 的 preflight、cli、runner | 无推理预检及公开动作路由 | 公开 validator CLI | 文件交接、独立解释器；新增约 130 行 |
| precision-align 的 preflight、delegation、runner | 逐项预检、局部失败与结果绑定 | 总入口/独立阶段；跨 Skill 仅 CLI | 同阶段证据与公开 validator；新增约 80 行 |
| customer_workflow 的 evaluation/datasets、compile/assets、compile/accuracy、runner | 准备回执绑定正式 catalog 后生成配置和预算 | 客户入口；内部模块私有 | effects 公开 CLI 委托，不导入精度私有实现；新增约 100 行 |
| tests/vllm-model-migration/evaluation/ | 数据准备与精度交接的行为回归 | 标准库 unittest | 临时资产、效果边界替身；不启动模型 |

原始布局准备、正式 catalog 和各项 preflight 均为分支局部失败，只阻塞依赖该数据的消费者；其他就绪项继续。共享证据写入失败与取消仍为任务致命。预检通过不等于有效测量，不产生评分结论；正式执行仍重新检查身份。

任务台账：规则/基线已核对；复现测试、实现、针对性验证、完整回归、原生加载验收、审查与契约收尾依次推进。Git 提交、合入、跨仓同步不在本次执行范围。修改前结构审计为 0 errors / 168 warnings，使用既有基线，不上调硬阈值；新增职责进入对应能力模块，避免继续堆入 runner。

### 实现、验证与审查结果

准备阶段先核验来源，再复制并逐文件校验 SHA256；C-Eval 依据实际科目、所选 split 与 CSV 表头生成 README，GSM8K 仅复制普通版 train/test。最终回执分别记录来源和派生目录身份。真实原生测试发现 C-Eval 自动推断会改变 id 类型、异构表头会给 val 添加额外列，故 README 明确所选表头的字符串类型并关闭默认 NA 转换；CSV 原字节保留，样本比较未放宽。

新配置只消费准备回执路径；旧请求和正式冻结共用 bind_config，省略则补齐、有效显式路径保留、冲突报出两条路径且原输入不变。正式 catalog 使用本次回执及解释器，重新计算所选样本长度；准备前观察只提供候选信息。raw→evalscope 布局升级创建新证据，原历史保留。阶段编排先预检全部就绪项，再运行通过项；独立失败不会取消其他项。仅转发真实推理 POST 后记录 evaluating，后台进程启动不视为已开始评测。

新增20项行为回归经历 RED-GREEN，完整278项通过，无跳过；233 Python/42 JSON/2 shell 静态语法、20 help、12开发技能/37资源、73文档检查及 git diff --check 通过。真实既有 EvalScope 1.14.1 环境中，两个 C-Eval 科目与 GSM8K 各15条完成自动准备、预检、正式 catalog 和冻结，样本身份、顺序、prompt、答案映射一致；路径冲突、额外文件、README/CSV修改和旧回执摘要全部阻塞。具体证明边界见[验证记录](TESTING.md#2026-11-03-数据准备与评测交接)。本次没有模型推理、精度评分或完整客户端到端运行。

结构审计从259文件/43目录/1647函数变为267文件/43目录/1703函数，最终0 errors/173 warnings。新增生产模块实际分别为 native_layout 61行、data 100行、precision/preflight 77行、alignment/preflight 49行、evaluation/datasets 58行。对应目录文件数为 dataset_prepare 7→8、runtime/evalscope 11→12、precision 11→12、alignment 10→11、customer evaluation 8→9；测试 evaluation 5→8，没有新增平铺根目录。新增生产函数参数最多6个；修改的既有公开/私有函数参数最多仍为7个，samples.verify 3→4仅允许复用已加载样本。

native_catalog复杂度14→11、CLI main 12→11；samples.verify 11→12、委托receipt 13→15，仍保留在各自样本身份/公开回执核验职责中。新增软提示为 bound_subset 11（同一正式catalog交接校验）、ceval_metadata 11（实际科目/文件/表头映射）、delegate 11（预检独立证据路径）、alignment目录11文件及布局测试复杂度11；均审查为本次能力的内聚扩展，没有跨Skill私有导入、通用框架或阈值豁免。普通审计发现的指标收紧要求通过工具 write-baseline 更新，未使用 approve-baseline-increase，策略未改。

最终审查补齐了预检取消后磁盘回执仍为 running 的缺陷：中断回归先失败，再验证取消写入 failed、退出1且精度结论仍为 not_evaluated；阶段消费者识别该失败，保留诊断。复核后无剩余可行动发现。规则/基线、复现、实现、离线与原生验证、审查及文档收尾 succeeded；完整客户实机精度仍 pending，提交/合并/同步、长期记忆和无关服务器问题 out-of-scope。原始数据、部署证据及历次失败尝试均保留。

## 2026-11-03 官方默认数据目录优先复用

本轮基于本地 dev 的 3104da6，仅改指定数据路径不存在时的来源衔接。用户已确认：官方默认目录只作为复用候选，新下载仍写 task.json 指定路径。Agent 查阅官方来源后提供默认目录及固定资料引用；不猜共享缓存根、不扫描其他账户、不把 URL 当本地路径。已有默认目录内容不可读、缺文件或身份不符时保留失败，不覆盖、不转为缺失后下载；无默认目录或目录确实不存在时使用原 HTTPS 下载器。

### 修改前职责与失败边界

| 文件 | 所有者与职责 | 消费者/依赖 | 修改前与预计增长 |
| --- | --- | --- | --- |
| dataset_prepare/resolution.py（新增） | 数据准备：验证官方默认目录证据、尝试本地读取并决定是否使用原下载器 | prepare 公开 CLI；同包 assets/sources | 约100行，明确来源选择效果；不进入精度 SDK |
| dataset_prepare/request.py、results.py、prepare.py | 可选 default_local 的形状、候选/证据路径保护、入口组装 | 既有公开请求；仅同包依赖 | 原126/49/59行；normalize_request复杂度14、output_parent 8、execute 3，参数2/3/3；不扩展既有列表热点 |
| runtime/evalscope/catalog.py | 路径缺失时给出先查官方默认目录、再下载到请求路径的指引 | 原公开 catalog；无下载效果 | 原141行，inspect复杂度4、参数2；元数据模式也返回缺路径指引 |
| tests/vllm-model-migration/evaluation/test_dataset_source_resolution.py（新增） | 公开准备与发现入口的来源决策回归 | 合成本地资产、仅替换 HTTPS 传输效果 | 同组新增约250行，不增加测试根平铺数量 |

失败边界：官方资料缺失/摘要变化、默认数据不可读或不完整均为本数据项局部失败；其余独立项沿用现有编排。明确不存在是允许下载的条件，权限错误或读取异常不是。证据写入失败与取消沿用既有任务失败合同。源码根与两个数据目录、资料文件均受输出保护；原始 task.path 不回写，实际评测仍绑定最终准备回执。

### 实现、验证与审查结果

默认位置只作为有资料依据的复用候选，来源 URL 与默认位置是否对应本数据集仍由 Agent 根据资料判断。脚本验证引用字节、文件内容及所选数据，不仅凭目录名宣称上游身份；本地复用的 upstream_status 仍为 unverified。下载包 expected_sha256 不能由本地目录证明，候选存在时明确拒绝绕过该约束。资料与来源身份在发布成功回执前复核，原请求及历史失败不回写。

首批13项测试先复现新字段及缺路径指引缺失；补充审查回归复现单文件候选误读父目录其他文件，修正为传递原候选文件而非验收数据根。最终19项专项、331项完整回归通过，无跳过；243 Python/42 JSON/2 shell 静态语法、20个help、12开发技能/37资源闭包、74文档及 git diff --check 通过。合成 HTTPS 响应只替换传输边界，实际目录创建、解压、格式/摘要检查及派生布局均执行；SDK发现边界使用替身。详细验收场景与命令见[验证记录](TESTING.md#2026-11-03-官方默认数据目录优先复用)，没有真实联网下载、SDK加载或模型/NPU运行。

结构审计由284文件/45目录/1788函数变为286文件/45目录/1822函数，0 errors/174 warnings不变。新增 resolution 为69行，dataset_prepare和evaluation测试目录各8→9文件，未扩展平铺根。request/results/prepare/catalog分别为126→149、49→52、59→60、141→145行；normalize_request、execute和inspect的复杂度/分支不变，语句各增加1、1、2；output_parent复杂度8→10、分支5→6、语句14→17，新增部分仅保护候选数据及资料边界。新增生产函数最多2个参数，既有函数参数不变；resolution最大复杂度5。normalize_request既有复杂度14和normalize_lists既有15保持原职责，新增字段校验独立为复杂度7的函数，未继续扩展列表热点。

来源选择仅依赖同包assets/sources，公开prepare组装核验，catalog不导入准备私有模块或下载数据；客户配置仍通过公开准备回执绑定。普通结构审计无硬违规或基线退化后，用工具write-baseline登记新增文件和指标，再运行普通审计；没有调整策略或使用approve-baseline-increase。最终审查没有剩余可行动问题；未来真实官方默认位置的格式/来源与网络下载仍须按任务独立验收。

任务台账：规则/基线、RED-GREEN、完整回归、静态/入口/文档/结构验证、审查与契约收尾 succeeded。本轮工作区位于 codex/dataset-default-local-reuse，尚未提交、合并或同步；Git状态随后续操作以历史为准。运行态验收 pending；长期记忆、跨仓同步和工作区清场 out-of-scope，未改动已有其他worktree、运行资产及证据。

## 2026-11-03 GSM8K 本地子集布局兼容

本次从本地 dev `3eba9702b3f666999204929743d44b8cb7891022` 建立 `codex/gsm8k-local-subset-layout`。目标为客户 subset_list=["default"]、["main"] 和整数 -1 加载同一测试文件，limit 继续决定前 N 题或全量。选择在准备阶段生成明确元数据；未采用改写客户子集名称或替换 EvalScope loader 的方案，以保留原生调用和证据身份。

### Change Shape Map 与边界

| 文件 | 语义所有者与单一职责 | 消费者、可见性及允许依赖 | 修改前与预期增长 |
| --- | --- | --- | --- |
| dataset_prepare/native_layout.py | 数据准备：生成默认 main 元数据并登记非样本 README | prepare.py；私有；标准库及本包 assets | 原61行，预计约20行，无新生产模块 |
| evaluation/test_dataset_layout.py | 公开准备 CLI 的元数据、原文件不变及摘要保护 | 标准库回归；测试内部 | 原131行，约50行；同组扩展 |
| evaluation/test_dataset_source_resolution.py | 默认目录复用的派生文件清单 | 标准库回归；既有消费者测试 | 仅更新包含 README 的预期集合 |
| evaluation/gsm8k_native_check.py | 显式运行的真实 SDK 加载与 limit 矩阵 | 维护者命令；已有 datasets 或 EvalScope 及被测入口 | 新增149行；evaluation目录9→10文件 |

GSM8K 派生 README 声明唯一 main 配置、default=true 和 train/test 精确映射。只复制标准 JSONL；来源 README、socratic 文件和原始题目不改。派生检查在内部登记 supporting_files，README 的大小及摘要进入最终 asset.identity，rows=0，不进入 selection；公开请求仍不允许 layout=evalscope 与自定义 supporting_files 混用。raw 模式、原始 expected_identity、客户输入及三文件格式均保持既有语义。

新增效果仍位于原生布局所有者：README 写入或复核失败沿用既有该数据项失败边界，保留部分尝试，不覆盖源数据。历史准备回执不回写；旧 GSM8K 目录需通过新准备生成可绑定的目录与摘要。没有自动迁移旧结果或扩大到模型、设备、安装及下载操作。

### 验证、增长与限制

RED：布局专项的元数据缺失和摘要保护断言失败，真实 datasets 4.8.4 loader 对 main 报配置不存在。GREEN：10项布局专项、334项全量标准库回归通过；21组真实 datasets 加载矩阵覆盖三种子集选择和 limit 缺失/null/-1/1/3/5/99。合成 test 为5题，分别选择5/5/5/1/3/5/5题；真实准备选择、最终评测 limit 与 sample_ids 一致，三种写法的样本内容和顺序摘要一致，未混入 train/socratic，源文件字节不变。命令与证据边界见[验证记录](TESTING.md#2026-11-03-gsm8k-本地子集布局兼容)。

本机没有 EvalScope 包。datasets 模式不声称实际 adapter 已发现 main 或原生评测器已应用 limit；脚本另提供 --backend evalscope，在已有评测环境调用原生 adapter 和本 Skill 逐样本 verify。该模式、正式推理/评分及服务器验收本次未运行，没有安装依赖或下载数据。

native_layout 61→72行；prepare_asset 的复杂度9不变、分支6→7、语句19→20、参数仍3；新增 gsm8k_metadata 的复杂度1、语句1、参数0。源码目录仍9文件；测试目录9→10。既有 ceval_metadata 复杂度11保持原样；未新增软提示。整体审计0 errors/174 warnings，287文件/45目录/1834函数；基线通过工具登记新增检查和当前度量，未调整阈值或上调硬热点。

最终静态语法243 Python/42 JSON/2 shell、20个公开入口、12个开发技能/37项资源闭包、74份文档及 git diff --check 均通过。审查覆盖来源不变、内部支持文件与公开请求边界、原生读取参数、limit及sample_ids、旧回执保护，没有剩余可行动问题；完整 EvalScope 运行态仍按上文保留未验证项。

交付范围为工作区实现和验证，未提交、合并、推送或跨仓同步；提交状态以 Git 历史为准。代码和文档按本次验证核对，规则保持不变；完整 EvalScope 运行态 pending，长期记忆及清场 out-of-scope。
