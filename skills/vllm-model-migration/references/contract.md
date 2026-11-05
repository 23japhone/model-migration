# 总入口请求、计划与交接契约

## 完整流程的文件交付

意图涉及性能调优时，总入口选择完整 `deployment_precision/run`，完成准备、模型迁移和有效精度基线，再调用独立导出入口。所有完整流程都必须完成[三 JSON 交接](tuning-handoff.md)；部分流程和历史分析不要求该交付。

原 `migrate.py` 的请求、阶段回执、状态和退出码保持兼容。其 completed 只表示原阶段完成；整个 Skill 的联调交付还需 `export_handoff.py` 返回 ready。导出仅消费公开回执和显式候选配置，不执行部署、评分或调优。补交配置可独立运行，不修改原 manifest。没有 GPU 基线或精度目标不阻塞交接；已声明精度目标 fail 仍保留原结论和原退出码，只要基线测量有效即可导出。

## 当前实现边界

总入口提供客户 task 模式、静态计划、完整链路、仅准备、仅部署、已有服务精度与历史分析，以及适配后继续、finalize 续接、服务策略和末端导出。[客户模式](customer-task.md)新增 --task、--context、--resume，与旧 --request 互斥；其数字 -1 语义不改变旧请求。EvalScope 支持完整三文件；显式 AISBench 保留精度执行但本轮不交付三个 JSON。随包资源和独立脚本检查不代替真实模型验收。

公开 CLI：`--request --work-root --run-id --action run|analyze --plan`，action 默认 run。Python 3.10+ 标准库。整个执行发生在调用该 CLI 的主机；不接受远程路径冒充本机路径。

### 静态资源路径契约

完整总 Skill 目录是唯一运行资源根，`scripts/skill_paths.py` 的 `plugin_root()` 沿用函数名并返回该根，不表示宿主插件。`references/skill-layout.json` 只声明 Skill 名称、层级和入口；布局中的 `root` 是 Skill 名称，总入口的 `path` 为 `.`，其他 Skill 路径相对于总 Skill 根，入口脚本和示例路径仍相对于各自 Skill。仅根 Skill 定位允许 `.`，其他相对路径继续拒绝越界和未规范化写法。运行不读取或生成构建资源清单，也不依赖 Git 或 `.gitignore`。

静态资源由布局中声明的各 Skill 按以下角色发现；每个 Skill 的 `SKILL.md` 必需，所属 `skills/` 子树由子 Skill 单独负责。

| 直属资源目录 | 纳入的文件类型 |
| --- | --- |
| scripts/ | .py、.sh |
| references/、reference/ | .md、.json；保留 adapt-agent 的单数目录 |
| assets/ | .json、.py、.template |

目录内递归纳入合法普通文件，稳定排序；缓存、日志、系统元数据和维护目录按实现中的明确规则排除。资源角色内的未知类型、符号链接和特殊文件拒绝，不把错误内容静默当作资源。布局之外的根目录文件和维护资料不复制；完整副本中出现未登记的嵌套 Skill 则拒绝。新增文件和内容修改会自动反映，新增资源类型或新 Skill 仍需更新相应规则或布局。运行请求和凭据不得放入静态资源目录，类型规则不能识别任意 JSON 中的秘密内容。

`owned_files()` 仅返回所选 Skill 直接拥有的当前资源；共享路径模块、布局及结果 Schema，以及所属 Skill 的声明入口和固定辅助资源必须存在。固定项由 `SHARED_REQUIRED` 和 `EXTRA_REQUIRED` 维护。缺少未选中任务的入口不阻塞其他独立任务；完整 `bundle_files()` 则要求全部声明 Skill 完整。删除非必需文件后不保留陈旧名单，必需文件缺失明确失败。

`resource_snapshot()` 读取当前普通文件，返回相对总 Skill 根的路径到 SHA256 映射。准备阶段沿用现有 resources 字段，注册沿用实际资源身份记录；实际运行仅在包外写回执，不回写 Skill 目录。注册复制使用同一文件集合，前后核对源和目标的集合与字节摘要；变化使本次复制失败，不自动重试或抹除部分证据。这些检查不是文件系统锁或可信来源证明。旧平铺注册目录继续支持普通文件，排除同类缓存/日志/维护项并拒绝链接及特殊文件。历史回执保持原始字段和摘要，不因当前资源变化重写。

全部入口无条件保护完整总 Skill 根，不以 `plugin.json` 的存在决定范围。目录搬迁后不得通过扩大资源根到仓库根或设置 PYTHONPATH 掩盖缺失依赖。

## 顶层请求

| 字段 | 契约 |
| --- | --- |
| schema_version | 必需，固定 1.0；沿用共同外层，不修改其他入口。 |
| scope | 必需：prepare、deployment、deployment_precision、precision。历史分析使用 precision 和 CLI action=analyze。 |
| stages | 可选阶段对象，键只能为本范围允许的 preparation、dataset_preparation、migration、precision。缺少必需节点时计划列 missing_inputs，不假设用户要求从头运行。 |
| service_retention | 可选 keep/stop，默认 keep；仅准备和历史分析不允许 stop。完整链路中先 keep，再按最终策略收尾。 |
| performance_target | 可选非空文本，保留用户原意，不解析或验证性能，结论始终 not_evaluated。精度目标由被引用的精度任务请求定义，不在顶层复制另一套。 |
| timeout_seconds | 可选，1—86400 秒整数，默认 14400；用于单次阶段 CLI 等待。计划不等待阶段。 |

拒绝未知字段、重复 JSON 键、非标准 NaN/Infinity、错误类型和非法引用。顶层请求必须为普通文件，使用 UTF-8 JSON；拒绝 FIFO、目录、非法编码及无法编码为 UTF-8 的孤立代理字符，合法 Unicode 转义对可用。输入文件、路径和说明不应含凭据；工具凭据留在已授权环境。错误诊断不回显顶层 JSON、argv 或 OS 异常原文，不承诺识别任意自由文本中的秘密。

每个阶段节点必须且只能含 `request` 或 `result` 引用；引用恰好为 `{path, sha256}`。path 是规范绝对文件路径，不使用 `..`、重复 `/`、NUL、文件系统根或 `//host` 形式；sha256 为 64 位小写十六进制。计划只核对写法，不检查文件是否存在、是否符号链接或摘要是否真实。运行时必须再核验。

| 节点 | request 消费者 | result 所指对象及使用条件 |
| --- | --- | --- |
| preparation | vllm-migration-prepare 的完整请求；仅准备时 purpose 由该请求定义，部署链中必须为 deployment | prepare-manifest.json；仅部署链可复用，必须覆盖模型、镜像和必需资料。 |
| dataset_preparation | 同一准备阶段，purpose=assets，只准备本次共享的数据任务 | prepare-manifest.json；覆盖本次数据消费者，不能把模型/镜像清单当数据清单。 |
| migration | vllm-migration-execute 请求模板；本次 preparation 字段由 preparation 节点绑定 | migration-manifest.json；仅完整链路继续精度时可复用。必须是 completed 且功能通过，包含完整部署交接。 |
| precision | vllm-precision-align 请求或允许交接字段留空的模板 | 不接受 result；精度测试或历史分析须显式提交阶段请求。 |

`prepare` 必须用 preparation.request；`deployment` 必须用 migration.request，保持本次功能验证。完整链路允许 migration.result，并不再要求 preparation；此时额外 preparation 是无消费者输入，应拒绝。复用含义是消费经过核验的证据，不是自动认可当前资产/服务状态。

## 精度绑定与真实依赖

precision.request 节点可另含 `bindings`：以精度阶段 evaluations 中的 id 为键，值仅含 `deployment: "migration"` 和/或 `dataset: "dataset_preparation"`。id 须为安全单层名称。部署绑定只允许在 deployment_precision 范围；该范围至少有一个评测显式消费 migration。独立 precision 可以只绑定数据或由自身请求提供完整输入。

数据准备节点必须有绑定消费者；存在绑定但没有对应节点时计划报告缺口。同一数据节点可以服务多个评测，计划只列一次。完整链路不强制所有评测共享同一数据：未声明 dataset 绑定的评测沿用其阶段请求中已有数据或阶段自有 datasets 依赖。

依赖关系固定，不接受任意命令、入口路径、DAG 或任意 JSON 字段替换：

- preparation → migration（migration.result 复用时无此依赖）。
- migration → precision 中显式部署消费者。
- dataset_preparation → precision 中显式数据消费者，与迁移无依赖。
- 已有服务或历史分析只进入 precision，除显式数据节点外，不自动扩展上游。

运行只在冻结的包外副本中注入以下字段：迁移请求的 preparation；被绑定评测请求的 deployment/dataset。精度 deployment 消费迁移 manifest 中原始 verify/review **结果回执**，不是 migration-handoff 或裸 deployment-handoff；dataset 消费准备 manifest 中对应数据任务的**结果回执**，不是整个准备阶段清单。资源停止另用 deployment-handoff。所有消费者继续按自己的公开契约核验。

实际运行须逐项核对 evaluations id 存在、数据准备只有一个明确选中的共享数据任务、依赖不存在双重声明；原字段已经完整指定时不得被新绑定覆盖。迁移请求原 preparation 与本次绑定同时出现也拒绝，不静默选择。未绑定字段保持原样，模型、设备、生成参数、评分和精度目标均不改写。子请求规范化和补齐后重新计算 SHA256；所有原文件不变。

### 评测工具透传与历史兼容

在本契约的旧 `--request` 模式中，evaluator 属于精度任务请求，总入口和阶段都不补写 backend/version，不导入评测 SDK 或兄弟 Skill 私有实现。新精度请求默认 EvalScope；显式 evalscope/aisbench、旧 Python 配置识别和省略 version/latest 的官方最新稳定版核验均由精度任务负责。旧明确版本保留为请求中的期望值，不解释为允许自动降级。环境缺失、索引不可达、版本或配置不兼容时，由原任务阻塞相应项，其他具备前提的精度项继续；不自动安装、升级、切换工具或重试。客户 `--task` 则按[安装契约](evalscope-installation.md)每次准备安装共用环境并绑定实际 Python 与版本，生成带实际明确 backend/version 的精度请求；不能将两种入口的准备策略混用。

analyze 的 source 支持原精度报告、evalscope-native-v1、aisbench-native-v1；没有 backend 的旧 AISBench 报告保留原 conditions，不因新默认值改成 EvalScope。历史分析不导入 SDK、不读取当前最新版本、不发送推理或重新评分；缺少原始身份/执行证据仍按原任务契约阻塞或待复核。明确续接和 finalize 复用只绑定允许的 deployment/dataset，不改变 evaluator、目标或原始配置。

总入口原样保留精度阶段的 results，包括实际 conditions。conditions 须与同项原始任务 detailed_report.conditions 一致；旧回执两侧都缺少该字段时按 null 对照，不补造身份。任一侧增加、删去或改写实际条件均拒绝；measurements/comparison/uncovered 继续按原契约绑定。原生指标和单位不统一改名，跨工具相对比较仍由精度任务拒绝。

原始精度回执的 measurements 必须为列表，每项为对象，valid 为严格布尔值，metrics 为对象；conditions 为对象或 null。completed 必须包含非空、有效的测量及非空指标，真实零分仍为有效值；failed/blocked 可以没有测量。即使阶段投影、台账、handoff 和摘要一致，也不能将空测量或畸形完成回执解释为有效评测。这里只核验公开结构和一致性，不重算原生评分。

若模型准备后仍缺迁移所需模型文件清单、实际载体或其他运行输入，应 blocked 并交付准备成果，Agent 基于真实产物补齐新请求；不从来源链接猜测这些输入。不把静态模板 ready 当作完整流程已可自动执行。

## 静态计划输出与退出码

计划 stdout 为单 JSON，退出 0 表示计划生成成功；缺输入仍退出 0，使用 ready=false 和 missing_inputs 明示，不代表业务可执行。

- 共同字段：schema_version、task_id、run_id、operation、inputs、status=not_run、success=false、conclusions、evidence={}、uncovered。计划不生成 manifest 或 handoff。
- ready：仅顶层必需节点和绑定已齐备；不检查子请求形状、工具、官方资料、资产或服务。
- execution_available：当前版本是否支持所声明范围，与 ready 是两个维度；不验证阶段是否已安装或子输入是否有效。
- stages：固定顺序的节点列表；每项含 id、skill、entry、action、mode=execute/reuse/missing、status=not_run、depends_on，准备节点另含 purpose。列表顺序仅供呈现，depends_on 才是失败传播边界。
- missing_inputs：缺少的节点或请求/绑定字段；不得将缺项改写为 skipped。
- evidence_parent：计划中的运行目录，不会创建。运行目录必须在完整安装资源外，拒绝符号链接重定向；实际运行另查来源、模型、数据和构建根。
- resource_finalization：none、keep 或 verify-agent:stop_owned_after_consumers；后者仅描述收尾策略，不证明资源 owned 或可以停止。

run/analyze 不带 --plan：缺顶层必需输入退出 3，返回静态缺口、status=blocked、next_action=provide_missing_inputs，不创建尝试。合法范围进入独立运行。顶层输入或输出边界非法退出 2，stderr 给出通用诊断。--help 只输出帮助。

当前运行退出码：0=交付完成且适用目标通过；1=执行/证据失败、取消、超时或目标未通过；2=请求/输出边界无效；3=必需条件不足。有效评测目标 fail 可以同时 status=completed、退出 1；不能仅按退出码汇总。

## 当前运行产物与状态

总入口运行目录为 `work-root/run-id/vllm-model-migration/UUID/`，交付 entry-manifest.json、task-ledger.json、entry-handoff.json 和 summary.md，保留冻结输入、阶段 stdout/stderr 与回执引用。各阶段的 implementation.json 固定实际入口脚本摘要及其自报 skill_version（未自报为 null）；不将总入口候选标识冒充旧阶段或发布包版本。

总入口保留原始顶层请求引用及冻结副本。阶段使用同一 work-root、总入口 run_id 前 40 字符加随机后缀的独立阶段 run_id；其 UUID 产物由台账引用，因此不会覆盖前次运行，也不会改变准备任务相对 work-root 的资产路径语义。阶段产物不强制放进总入口 attempt；搬迁运行证据须一并保留台账引用的文件。

准备请求副本只展开任务请求/版本资料的相对路径、固定公开默认值；原任务计划负责业务规范化。无效单项保留原阶段的 error/blocked，不扩大为其他资产的失败；只接受 plan/run 一致的任务输入及 required 声明。仅部署模板只注入 preparation，已有同名字段时阻塞且不启动准备。准备阶段 plan 的分项阻塞允许进入实际准备以保留其他任务成果；迁移/精度计划必须通过且身份匹配。

旧准备 result 先核对原始 manifest、台账、handoff 和任务证据，再用其固定请求通过准备公开 CLI 重新核验；不会直接宣称旧资产当前有效。已有服务精度禁止阶段 deployments，也禁止评测条目的 deployment 依赖名；任务请求中现有 deployment 回执照常保留，由精度任务复核。精度阶段自有 datasets 依赖可用，历史分析禁止这些准备依赖。

来源扫描只读取 JSON 元数据及证据，不执行评测配置、不调用服务。收集所有可读来源/资产保护边界后才能创建总入口目录；摘要不符的可读声明仅用于保守保护，不能成为执行输入。调用者仍须确保请求、路径和原始 stdout/stderr 不含凭据；运行证据保留原文，不承诺任意秘密脱敏。

两种原生格式、声明 config_index 的来源及其索引所在目录都属于保护边界。即使 format 畸形、索引缺失或摘要不符，也先收集可读声明中的目录和资产，再拒绝输出重叠；保守收集不表示该来源有效，不提升其执行状态。

当前文档的目录保护先于递归遍历；单个元数据分支触及扫描深度或路径无法规范化时，仍继续收集其后同级的可读引用和资产。原来源的业务有效性继续由公开阶段/任务校验，不能将保守扫描当作来源已通过。任何已收集的来源/索引/资产与输出重叠，均在创建总入口 attempt 前拒绝。

阶段回执按各自公开格式核对 schema/task/run/operation、输入、退出码、stdout 与落盘结果、原始任务及证据。准备阶段旧 handoff 没有台账摘要，不伪造其保证；迁移/精度 handoff 必须绑定最终台账。精度原始文件摘要可位于 detailed_report.files，不能因为 evidence 为空误拒绝有效结果。同时核验请求任务集合、required、准备资产投影、版本门禁和迁移预检路线；注册必需时须有通过的原始注册回执。精度结果的 measurements/comparison/uncovered 必须与原始任务一致，handoff 的身份、任务、结果、资源必须与 manifest 一致。目标和比较按适用任务聚合，失败不能被其他项 pass 掩盖；没有目标或 GPU 基线不得形成相应已评估结论。SHA256 固定证据身份，不证明提供证据者可信或替代实机验收。

共同外层沿用 [task-result.schema.json](task-result.schema.json) 的 1.0。conclusions 分别记录 preparation、migration、functional_validation、precision_testing、evaluation_validity、comparability、precision_target、performance、lifecycle。操作完成与业务目标分开；未请求、未测量或不可证明的维度保持 not_evaluated。

先固定最终 task-ledger，再生成绑定台账 SHA256 的 handoff，最后原子发布 manifest。发布期间的隐藏 .entry-manifest.pending.json 不是可消费终态；取消在最终提交临界区之前到达时，先固定失败快照再发布。若失败快照也无法保存，则不留下成功 manifest。屏蔽信号并检查已接收及待决取消后，进入不可取消的最终提交临界区；rename 是对外可见的原子发布点。临界区内新到达的信号在提交后递送，不追溯重写结果；消费者必须以正式 manifest 及其绑定交接为准。发布完成后不修改台账。新尝试不覆盖旧尝试。回执需核对输入身份、schema/task/run/operation、状态、退出码、stdout 与落盘一致性、证据引用和终态台账摘要；SHA256 只是内容身份，不是签名或可信执行证明。

复用准备结果时，用其固定输入通过原准备入口复核现有资产；复用迁移结果时校验原始准备、迁移输入、任务、部署交接及 finalize 的原始待复核来源、批准 review 和本次 revalidated review；独立精度任务仍核对当前服务和权重。历史分析只能报告来源时的观察。服务状态记录观察时间和来源，不能将旧 retained 写成当前仍在运行。

适配缺口及 semantic_review_required 保留原阶段 next_action。migration.request 可显式提交 adapt-agent check 的完成交接，仍由迁移阶段核验适配范围。语义复核后提交 execute finalize 的 migration.result；精度消费 review_revalidated，不重新部署。旧 owned 结果的复用不会将服务所有权转移给本次总入口。总入口不写适配代码、不替代人工语义结论，不自动恢复或重试。

局部失败只阻塞真实消费者；独立成功产物保留。显式取消、共享证据无法保存或继续不安全时停止后续效果。取消先通知自有阶段 CLI 收敛，清理未确认时不能报告资源已释放。

迁移 verify 请求必须 keep，内层 stop 不能覆盖顶层策略。顶层显式 stop 在所有消费者结束后委托 verify-agent --action stop，只对本次迁移调用证明拥有的 owned 服务重新核对完整身份。borrowed 和 migration.result 复用的服务不停止。失败不进行全局清理；未完成适用收尾时不能宣称整体成功。

服务索引分别记录来源、原观察时间（来源没有明确时间时为 null）和报告时间；analyze 不把本次分析完成时间当服务观察时间。当前超时/中断先向自有阶段 CLI 发 SIGTERM，等待 30 秒收敛，必要时回收该 CLI 进程组；保留 resource_cleanup_unconfirmed，不直接停止模型服务。本次迁移调用在执行前固定 run/root/request 摘要；取消后只有相同调用的唯一终态迁移回执通过核验时才允许显式 owned 收尾。缺证据、身份变化或停止失败时保持 failed/unknown。发布终态期间延迟信号并在最终提交前记录取消；最终提交临界区屏蔽信号并先检查待决取消；此检查之后新到达的信号在提交后递送，不再改写该次结果。不将清理成功改写为操作成功。

精度台账另列 blocked_evaluations，只有依赖可用的条目进入精度阶段；删除对应无消费者的数据依赖。可运行条目的原始回执和测量继续保留，存在被阻塞条目时总阶段仍不算交付完成。生命周期记录 policy、ownership、original_ownership、state、来源及观察时间；stopped 必须来自经过核验的停止回执或来源中已经停止的观察，不把旧观察当本次服务存活证明。
