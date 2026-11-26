# 模型迁移特性开发规划汇总

本文说明 `model_migration` 的需求来源、当前功能范围、输入要求和完成标准。原始需求核对日期为 **2026-10-28**，精度对齐专项规划于 **2026-11-12** 整理，运行数据采集、任务质量、Token 预测及张量与算子对齐的实现状态于 **2026-11-25** 核对。具体字段与执行规则以各 Skill 的现行契约为准。

Codex 的开发与 Git 规则见 [AGENTS.md](../AGENTS.md)，完整治理文档从 [PROJECT_BRIEF.md](PROJECT_BRIEF.md) 导航。当前检查结果见 [TESTING.md](TESTING.md#当前验证基准)，后续缺口顺序见 [ROADMAP.md](ROADMAP.md)；这些文件不另行定义需求，也不把本文的需求描述转化为自动实施授权。

当前有 14 个嵌套 Skill。精度阶段可比较任务成绩，并对明确未达标的样本做 Token 预测对齐。客户输入、精度续接和三文件交付沿用原有入口。[任务质量与预测采集契约](../skills/vllm-model-migration/references/task-quality.md)

## 1. 需求背景与文档用途

需求最初来自《模型迁移特性开发规划》图片的文字整理，涵盖期望输入、准备工作、模型迁移、精度对齐和 Skills 设计诉求。原始输入已在第 3 节完整列出，当前任务要求与原始规划取舍分别见第 4、6 节；维护者可直接在本文查阅所需的需求说明。本次结合现行技能契约整理，没有重新核对原始图片。

本文区分三类内容：

- **原始规划**：保留需求脉络，不把其中每一项当作当前待开发任务或实施授权。
- **当前纳入范围**：说明本套 vLLM / vLLM-Ascend Skills 应完成的业务及验收边界；迁入后的运行验证另行记录。
- **后续确认的补充需求**：来自现行契约，例如完整流程的三个交付 JSON，不追溯写成原图已经提出的要求。

精度专项拟复用哪些目录、拟新增哪些模块，以及 11 项任务各由谁负责，见[第 4.3.3 节](#433-精度对齐目录架构与任务归属)。已实现的目录、调用关系、资源边界和路径契约由[架构文档](ARCHITECTURE.md)维护，新模块落地后在那里更新。

## 2. 当前功能范围

本套 Skills 面向昇腾环境中的 vLLM / vLLM-Ascend，覆盖资产准备、支持度预检、按需模型实现适配、框架注册、部署功能验收、精度测试及三文件交接。用户可以请求完整流程，也可以直接使用独立阶段或任务。

精度阶段已纳入“精度测试”，以及“精度比较”中的 EvalScope 任务质量对齐。服务链路三步定界可独立调用。已可从质量未达标分项定界到首个异常 Token，并在单卡同步 Eager 下按清单运行单请求张量与算子诊断。[第三遍取证与复算](../skills/vllm-model-migration/skills/vllm-precision-align/references/tensor.md) · [专项进度及验收要求](#432-精度对齐专项开发规划)

SGLang 迁移与注册、框架自动选择、完整的运行异常分类诊断体系、自动修复重试、性能优化与正式压测均不属于当前范围。原规划中对应条目在第 6 节保留，不因此成为必须补齐的依赖。量化、训练、多机调度和 Agent 宿主插件发布也不纳入本仓交付。

## 3. 输入要求与适用条件

原文列出的七类输入是需求清单，**不是所有请求都必须填写的统一表单**。实际必需项由所选任务与契约决定。

客户完整流程输入 prompt 与 task.json。普通模板及可选的 `accuracy.gpu_baselines` 见[客户契约](../skills/vllm-model-migration/references/customer-task.md)和[任务质量示例](../skills/vllm-model-migration/assets/task-quality.example.json)。客户参数简化不代表底层阶段可以省略运行身份、资源预算或评测条件。

| 原始期望输入 | 当前处理方式 | 适用条件与限制 |
| --- | --- | --- |
| 模型链接 | 接受明确的模型来源、revision 或已有本地资源，核对配置、权重及文件身份 | 模型准备、部署等场景按任务提供；仅支持度预检可以先使用元数据，不强制先下载完整权重 |
| 测试集链接 | 明确数据来源或本地路径，以及 split/subset、样本选择和评分所需格式 | 精度测量需要有效数据；可以复用已核验数据，仅部署不强制准备测试集 |
| 模型部署框架 | 当前范围固定为 vLLM / vLLM-Ascend，记录本次实际版本和运行环境 | 不提供 SGLang 路线或自动框架选择，不把框架名称当作兼容性证明 |
| GPU 启动参数 | 作为基线溯源和配置对照资料，保留原始参数及 NPU 侧调整依据 | 不是所有 NPU 部署的前置条件，不能原样拼成 NPU 启动命令；参数本身也不等于有效 GPU 精度基线 |
| 精度目标 | 明确指标、单位、方向及适用的绝对目标或相对基线条件 | 声明达标需要明确目标；无目标仍可取得有效测量，但目标结论保持未评估 |
| 性能目标 | 可以原样记录，当前性能结论为 `not_evaluated` | 不执行性能达标验收；客户交接负载由 Skills 自动生成，旧导出接口继续接受明确候选 |
| GPU 逐层 L1 norm 值 | 现行精度测试不要求提供 | 下一阶段拟用双端 L1 记录缩小异常范围。任务质量对齐可独立执行 |

完成实际任务还可能需要目标主机、运行载体、设备与归属、镜像身份、软件版本、资产位置、证据工作根、评测解释器和工具配置。这些是现行契约的执行条件，不宣称全部来自原图，也不统一提升为总入口开始前必填。

普通精度测试可独立取得 NPU 成绩并按其已有契约判定绝对目标。任务质量对齐使用可比 GPU 成绩和相对目标。输入、示例和历史配方中的数值不能未经确认成为本次默认参数。

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

### 4.3 精度对齐

#### 4.3.1 精度测试（现行）

[vllm-precision-align](../skills/vllm-model-migration/skills/vllm-precision-align/SKILL.md) 负责组织输入、委托和汇总，[verify-agent-plus](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/SKILL.md) 负责原生评测或已有产物分析。部署、数据准备和精度评分保持各自唯一归属。

当前要求如下：

- 新测量默认使用 EvalScope，兼容显式 AISBench 和旧 Python 配置；工具按现行任务契约识别与校验。明确版本按精确期望核验，省略版本或使用 `latest` 时才在实际运行环境核验官方最新稳定版，不把所有请求改成强制 latest。
- 使用实际工具原生配置、模板和评分，保留数据、样本、生成条件、结果、日志和指标来源。客户 EvalScope 每次准备调用原版安装脚本，默认主机共享 /data/evalscope-venv；可选参数仅进入 Agent context。实际环境变化要求新任务；AISBench/资产 SDK 和旧内部请求的环境前提保留，不切换工具。安装检查不替代原生精度验收。
- 对照 GPU 与 NPU 成绩时，核对模型与权重版本、数据集与样本范围、生成参数和评分方法。直接比较分数要求评测工具相同、指标含义和单位一致。引用公开成绩时，记录谁做了这次评测，以及来源是否说明了评测所用的设备。第三方明确使用 GPU 时，这一设备信息只适用于第三方公布的那组成绩。
- 测量有效性、基线可比性和目标达标分别判定。有效评测未达到目标时交付 `fail`。Token 对齐从质量报告选择有据可查的题目，并按预测包的可用情况执行比较或定向重跑。
- 缺少数据或评分配置时不能用任意聊天请求代替精度测试；缺 GPU 基线不宣称对齐，缺目标不宣称达标。

公开数据的详细程度决定了任务质量能比较到哪一步。只有总分、分项分数或综合成绩时，可以对照相同条件下按同一方法计算的分数。若还公布了每道题或每个检查项的评分，可按题目或检查项编号核对哪些结果发生了变化。若同一轮评测还提供了完整原始回答和评分规则，就可以逐题复查回答及评分。每种比较只覆盖资料明确列出的测试范围。

原图中的“通过 AISBench 和开源数据集测试精度”保留为来源背景，不作为当前工具唯一选择或新仓验收事实。具体配置及工具支持边界见[精度任务契约](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/references/contract.md)。

#### 4.3.2 精度对齐专项开发规划

下表依据《智谱精度对齐》第 3 页“精度对齐特性开发规划”，列出专项内的 5 个任务阶段、11 项工作。项目整体仍按第 4.1 至 4.3 节组织。表中的“准备工作”负责核对评测条件和比较资料，需要模型或数据资产时复用第 4.1 节的准备结果。各项工作的主责模块和交接产物见[精度对齐目录架构与任务归属](#433-精度对齐目录架构与任务归属)。

**期望输入**包括模型、测试数据集、模型部署与评测配置、开源任务基线、GPU 运行数据、CPU 高精度参考和精度目标。任务质量对齐使用可比的成绩和评测条件。Token 对齐使用同题的 GPU、NPU 预测，发现可比的异常位置后可用 CPU 单独复核。张量对齐读取该题的首异常回执、双端共同采集清单及逐调用张量。CPU 参考根据可核验的实际操作数和等价实现计算。

| 任务阶段 | 一级任务 | 二级任务 | 开发进度 | 任务详情 | 验收要求 | 优先级 |
| --- | --- | --- | --- | --- | --- | ---: |
| 准备工作 | 评测条件确认 | — | 已有基础，待完善 | 核对模型与权重版本、基线使用的 benchmark、数据集版本和样本范围，以及提示模板、生成参数和评分配置。 | 列出 GPU 基线与 NPU 评测条件的一致项、差异项和未知项，明确哪些成绩可以直接比较、哪些需要按基线条件重新评测。 | 1 |
| 准备工作 | 对比基线准备 | — | 已有基础，待扩展 | 核验开源任务分数的版本和设备来源，接入 GPU 的 Token 预测、层与算子数据，以及对应的 CPU 高精度参考。 | 逐层列出基线来源、可用数据范围、缺项和比较标准。任务分数注明指标、单位、方向和精度目标，数值比较注明适用的误差指标及阈值。 | 1 |
| 精度测试 | 数据集评测 | — | 已有基础，待扩展 | 按迁移任务指定的 benchmark、数据集及配置取得 NPU 成绩。若本次指定的就是开源基线的评测任务，沿用其评测条件。 | 已有 NPU 结果满足本次评测条件时可复用，否则运行本次指定的 benchmark，交付原生分数、实际样本范围及评分依据。任务质量对齐另按 GPU 基线选择评测。 | 1 |
| 精度测试 | 运行数据采集 | — | 独立采集工具、单卡 Token 与张量定向重跑已实现 | V1 同步 Eager 可按输出 Token 或实际前向查询 msProbe 张量及可选的缓存状态。单卡 Eager 文本批次也支持显式异步调度，按请求分别查询。普通注意力和 GDN 的单机采集按实际 TP 卡数工作，单卡、双卡已有实测；四卡、八卡目前为离线验证。MLA 已验收单卡。单卡 ACLGraph 的文本请求、双卡单图与流式等场景分别按模型验收。 | 单卡 Token 重跑已关联原生评分、请求和预测。张量阶段沿该题的 Token 回执绑定目标前向与调用，双端实机验收待完成。[独立工具分项验收](MSPROBE_RUNTIME_CAPTURE.md) | 1 |
| 精度对齐 | 精度比较 | 任务质量对齐 | 已实现 EvalScope | 使用客户 GPU 基线，或按模型检索公开基线。自动检索缺少必要材料时记录未评估，继续其他任务。条件齐全时复用或重跑 NPU 评测。 | 按总分、分项和可选逐题评分分别报告有效性、可比性、目标和变化。后续诊断针对明确未达标的范围重跑并采集运行数据。 | 1 |
| 精度对齐 | 精度比较 | Token 预测对齐 | 已实现，单题 NPU 与合成 GPU 验证 | 在相同题目和前文下，逐步比较全词表原始 logits、选词前的归一化概率及实际接受的 Token ID。首个具有双端预测的异常位置可用 CPU 高精度参考复核。 | 任一组数值的最大绝对差超过该组容差、实际选词不同或一端提前停止时，报告该题首个异常输出位置（从 1 计数）。输出相同而停因不同时报告结束边界。记录、覆盖范围及缺项见[单题比较契约](../skills/vllm-model-migration/skills/vllm-precision-align/skills/vllm-precision-compare/references/token.md)。 | 1 |
| 精度对齐 | 精度比较 | 张量与算子对齐 | 单卡同步 Eager 已实现，双端模拟数据和 msProbe 钩子已验证。真实双端采集待验收，多卡对齐待开发。 | 按题目、前文、目标输出位置、调用与槽位配对单卡张量。核对形状、布局和类型后逐元素比较，具备等价实现和完整操作数的调用另做 CPU 复算。 | 按约定的误差指标和阈值报告已比较的调用、超限位置及对应的输入输出张量。列出漏采、截断或无法匹配的调用，使读者能复查结论覆盖了哪些计算。 | 1 |
| 精度问题分析 | 问题定位 | 服务链路的问题定界 | 已有实现，待验收 | 将标准三步走并入本任务。以原评测的异常请求和响应建案，检查请求、渲染后的模型输入，再对照诊断重放得到的原始生成与 Chat 返回。 | 用各环节的记录指出最早可定位的差异，区分原请求现场与独立重放。若记录不全，标出已核实的环节以及仍需补取的证据。 | 2 |
| 精度问题分析 | 问题定位 | 模型算子的问题定位 | 待开发 | 将逐层激活值诊断并入本任务。可用 L1 范数粗筛疑点，复用“张量与算子对齐”的逐调用比较结果，沿执行顺序追查输入、输出、权重及缓存状态。 | 区分最早出现偏差的调用与误差最大的调用，给出支持“输入已带入误差”或“同输入下本次计算出现误差”的对照证据。证据不足时，记录疑点和待验证项。 | 2 |
| 精度问题分析 | 问题重现与原因确认 | — | 待开发 | 保存异常现场的输入和运行状态，在独立诊断中重放可疑计算，并用参考结果和对照实验检验原因。 | 记录复现条件、是否复现、对照结果。原因得到验证时给出依据和修复建议，未能确认时注明尚缺的证据。正式评测成绩保留原始记录。 | 2 |
| 结果交付 | 精度对齐报告 | — | 已有报告，待扩展 | 汇总任务分数和三层对齐结论，列明各层的比较基线、目标与实际覆盖范围。发生异常时纳入问题位置、复现结果和修复建议。 | 读者可从每项结论查回对应评测或运行数据。三层达标时直接出具报告，有未评估层或待确认问题时如实列出。 | 1 |

##### 任务质量对齐的评测选择

客户声明的 GPU 总分、分项和逐题得分直接作为基线。没有客户基线时检索发布者的公开来源，按来源、数据集、指标和范围核实分数。NPU 使用该次任务的有效测量，阶段可以只读核验并复用有效历史测量，也可以按指定评测请求独立运行。当前原生测量入口支持 EvalScope。

任务质量报告按匹配的指标及范围列出 GPU、NPU 分数、指标方向、退化量、阈值及来源。阈值优先取显式值，其次为同指标同范围的发布者阈值，缺省时非负比例尺度按 GPU 分数的 1%，零基线或任意零点指标取 0。缺少有效分数的分项保留缺项；已确认失败的分项进入选题。

##### 复用范围与开发顺序

开发参考为 `model-agent` 仓库 `skills/verification/precision-alignment`（核对版本 `894442cd`）。复用范围是三步定界、逐层探针及比较工具。服务启停继续归 `verify-agent`，评测和样本证据归 `verify-agent-plus`，阶段结论由 `vllm-precision-align` 汇总。准备阶段共用一份条件和基线核对记录；数据集评测与任务质量对齐共用结果核验和评分证据，各自按上节规则选择 benchmark。

1. **优先级 1 为扩展定向重跑的采集范围。** 单卡同步 Eager 文本贪心请求已能从评分追到同题预测，再按首异常定向采集该次前向的张量。多卡及并发请求的定向对齐仍待开发。外部 L1 启动器会启用 Eager，默认只记录前两次前向，仍需在原部署模式下验收。[Token 单题验收](TESTING.md#2026-11-25-token-首个异常定位)
2. **优先级 2：将服务链路定界接入诊断流程。** 已有可复现的完整异常 Chat 请求时可独立调用 `three-step-demarcation`。先检查原始消息、工具和生成配置，再经服务端渲染取得 Token ID 并反解，最后用这些 ID 调 Completion 接口，将结果与原请求的 Chat 重放响应对照。两次生成分别保存输入、实际采样和停止参数。若回退到服务端 `/tokenize` 或本地模板，记录回退途径及尚未核实的工具、模板等请求字段。两次生成条件有差异时，报告注明哪些环节仍可定界。验收时用一条已知异常检查证据能否支持对应的请求、模板、生成或解析环节；九类定界标签用于选择排查方向，根因另行验证。
3. **优先级 1 为在同轮证据上验收 Token 和张量比较。** `logprobs-diff` 提供 API 返回的 top-k 候选排序，可辅助检查选词分歧。已实现的 Token 比较在相同前文下核对全词表原始 logits、处理后分数和实际选词。分数超限、接受的 Token 不同或一端提前停止时，报告最早异常位置；停止原因的差异单列在结束边界。真实 GPU 与 NPU 的结果仍需同条件设备证据。张量阶段按冻结清单逐调用比较双端文件，核验 CPU 参考的适用条件并报告漏采。[Token 比较契约](../skills/vllm-model-migration/skills/vllm-precision-align/skills/vllm-precision-compare/references/token.md)
4. **优先级 2：有可比张量后，接入算子定位和独立复现。** 可用 `l1-norm-probe` 对双端相同挂点的张量绝对值之和做粗筛，再按实际调用顺序用完整张量检查疑点。若某层输入已偏离，向上游追查；若输入、权重及缓存状态可比而输出首次超限，记录该调用为疑点。随后独立恢复异常输入和所需状态，保留参考结果与单变量对照实验，分别记录能否复现和原因是否得到验证。

##### 阶段交付与验收用例

首轮任务质量报告用于选题。二遍 Token 比较使用两端各自在同题复测中取得的预测记录。每端保留题号、实际输入输出 Token ID、各输出位置的全词表原始 logits 与处理后概率，以及停止原因。CPU 仅在具有双端预测的首个疑点复核。第三遍仅在目标位置有双端可比预测时建立共同清单，关联产生该预测的前向、调用及槽位。[最小预测记录](../skills/vllm-model-migration/skills/vllm-precision-align/skills/vllm-precision-compare/references/token.md#输入) · [第三遍采集契约](../skills/vllm-model-migration/skills/vllm-precision-align/references/tensor.md)

沿用本仓精度报告，逐层列出结论和证据。每层记录有效的双端证据和缺项。CPU 复算独立报告覆盖、误差或失败原因。三层均覆盖本次约定的范围并达到各自目标时，方可给出“三层精度对齐达标”结论。

针对参考版本已复现的三种误判，按以下预期结果验收。双端空日志且匹配点为零时，记录缺少可比数据；没有双端日志和 dump 时，逐层诊断标记为未评估；只有 L1 超限而没有 msprobe 比对产物时，报告只引用 L1 证据。另用一个早期轻微超限、后期误差更大的用例，验证报告能分别指出首个超限调用和最大误差调用。

出现超限时，按证据的详细程度交给后续精度问题分析。仅有任务分数时，交付指标差值、评测条件和样本范围。有逐题、Token 或算子记录时，再附差异题目、首个可定位的生成位置或超限调用，以及可复查的输入输出。后续分析据此定界服务链路或模型计算问题，通过重放和对照实验确认原因。

#### 4.3.3 精度对齐目录架构与任务归属

精度对齐沿用“总入口 → 阶段 → 独立任务”三层结构。`verify-agent-plus` 负责原生测量、历史复核和按题定向重跑，`vllm-precision-compare` 比较任务成绩、Token 预测和逐调用张量，`three-step-demarcation` 负责服务链路定界。统一诊断入口仍属规划。[现行三层职责与公开入口](ARCHITECTURE.md#3-三层职责与公开入口)

##### 现有目录与复用位置

下图只列本专项使用的现有路径。[完整实现布局](ARCHITECTURE.md#2-当前目录与资源归属)

```text
skills/vllm-model-migration/
├── scripts/migrate.py                            总入口，选择阶段并核验交接
├── references/                                   共用 Schema 和 Skill 布局索引
└── skills/
    ├── vllm-migration-prepare/skills/vllm-dataset-prepare/
    │   └── scripts/prepare.py                    准备数据集，固定版本、split 和实际样本
    ├── vllm-migration-execute/skills/verify-agent/
    │   ├── scripts/verify.py                     部署、功能验证、服务生命周期及独立采集入口
    │   └── scripts/verification/runtime/capture/ 已实现：Eager worker 采集、会话封存及 Token 反查
    └── vllm-precision-align/
        ├── references/                           精度阶段请求与交接契约
        ├── scripts/precision.py                  精度阶段公开入口
        ├── scripts/alignment/                    旧精度流程的依赖委托、回执核验和汇总
        ├── scripts/quality_stage/                核验或重跑 NPU 测量，委托质量比较
        ├── scripts/token_stage/                  按质量报告选题，按需委托重跑、比较与 CPU 复核
        ├── scripts/tensor_stage/                 冻结首异常采集清单，核验重放并委托张量比较
        └── skills/verify-agent-plus/
            ├── references/                       原生评测及历史结果分析契约
            ├── scripts/validator.py              评测任务公开入口
            ├── scripts/precision/runtime/        原生评测、HTTP 记录和样本核验
            └── scripts/precision/token_run/      单题复测并绑定评分、HTTP 请求和 worker 预测
```

##### 已有比较模块与拟新增的诊断目录

现有比较任务使用 `scripts/compare.py`。下图末尾的 `vllm-precision-diagnose/` 为后续诊断设计。

```text
skills/vllm-model-migration/skills/
└── vllm-precision-align/skills/                   现有父目录
    ├── vllm-precision-compare/
    │   ├── SKILL.md
    │   ├── references/token.md                   预测包、首异常位置和 CPU 复核契约
    │   ├── references/tensor.md                  双端张量、共同清单与 CPU 参考契约
    │   ├── scripts/compare.py                    任务质量、Token 预测与张量比较入口
    │   └── scripts/comparison/
    │       ├── quality.py                        对照原生分数、分项、逐题或逐检查项结果
    │       ├── token.py                          比较相同前文下的预测分数、选词和停止位置
    │       └── tensor/                           按清单配对张量、比较误差并复核 CPU 参考
    └── vllm-precision-diagnose/                   规划中的统一诊断目录
        ├── SKILL.md
        ├── references/                           异常请求、定位证据和独立重放契约
        ├── scripts/diagnose.py                   选择服务定界、算子定位或异常复现
        └── scripts/diagnosis/
            ├── service.py                        核对原始请求、渲染输入、生成与解析结果
            ├── operator.py                       按调用顺序追查激活值偏差的来源
            └── replay.py                         恢复输入和运行状态，做独立重放与对照
```

##### 调用与证据交接

1. **阶段选择测量或复用。** `vllm-precision-align` 按[任务质量对齐的评测选择](#任务质量对齐的评测选择)，委托 `validator.py` 只读核验可复用的 NPU 测量。无可复用结果时按本次配置独立测量。迁移任务自身的精度测试独立交付。基线来源或复测材料不足，只将受影响的相对目标列为未评估。
2. **Token 定向重跑关联评分与预测。** `verify-agent-plus` 对选中题目开启 `token_prediction` 会话，记录原生 EvalScope 请求及其实际响应，核对评分和 worker 的预测回执。GPU 与 NPU 按相同样本、前文和生成位置配对。第三遍按相同前文及逻辑输出位置配对，两端分别登记本端前向 ID、调用和张量文件。每端用本轮请求 ID 核对归属，双端请求 ID 可以不同。借用服务缺少挂点时记录覆盖限制，独立诊断产物单独归档。
3. **阶段按证据委托比较与诊断。** `compare.py --action tensor` 按清单报告首个超限调用、最大误差坐标及 CPU 复核。自动算子根因定位和统一的 `diagnose.py` 入口仍待开发。已有完整异常 Chat 请求时可独立调用 `demarcation.py` 做服务定界。后续将有可比张量的逐调用结果交给算子定位，独立重放只用于验证具体疑点。各模块向阶段交付带文件路径和 SHA256 的回执。阶段在台账、报告和交接文件中列出结论、覆盖范围和缺项。原始请求、张量及重放产物保存在包外工作目录。

旧精度 `run` 和 `analyze` 继续按 `evaluations` 及原台账契约处理。任务质量对齐使用独立的 `--action quality` 请求，包含 `baseline`、`native_request` 及可选候选回执。客户入口调度该动作并保存质量报告，三文件按原交付条件导出。Token 对齐使用 `--action token`，张量对齐使用 `--action tensor`，各自保存比较回执。[精度阶段契约](../skills/vllm-model-migration/skills/vllm-precision-align/references/contract.md)

##### PPT 第 3 页的 11 项任务与模块对照

下表与第 4.3.2 节的任务顺序一致。“主责模块与协作方”指本次设计中的归属，开发进度和验收标准见原规划表。

| 序号 | PPT 任务 | 主责模块与协作方 | 交付给下一环节的结果 |
| ---: | --- | --- | --- |
| 1 | 评测条件确认 | 精度阶段 `alignment/`，引用数据准备和测量回执 | 一份两端评测条件对照，标出一致、差异及未知项，供阶段选择复用或复测。 |
| 2 | 对比基线准备 | 精度阶段 `alignment/`，沿用第 1 项核对记录 | 按任务、Token、张量登记基线来源、设备、可用范围、阈值和缺项，绑定 GPU 记录及适用的 CPU 高精度参考。 |
| 3 | 数据集评测 | `verify-agent-plus`，按需复用 `vllm-dataset-prepare` | 本次迁移任务指定 benchmark 的有效 NPU 原生结果、实际样本范围和评分依据。 |
| 4 | 运行数据采集 | `verify-agent` 的 `runtime/capture/` 采集 worker 数据；`verify-agent-plus` 的 `precision/token_run/` 关联复测样本与预测 | 从评分和 Token 回执可追到单题第三遍的前向、调用、张量文件和覆盖缺项。每遍分别保留本次请求及证据。 |
| 5 | 任务质量对齐 | 客户入口选择 GPU 基线，`quality_stage/` 选择 NPU 测量来源，`verify-agent-plus` 取得或核验原生成绩，`vllm-precision-compare` 比较 | 复用或重跑 NPU 测量，每次按本次 GPU 基线、目标和请求的题号计算比较结果。 |
| 6 | Token 预测对齐 | `comparison/token.py`，可复用 `logprobs-diff` 的候选排序 | 相同前文下全词表原始 logits、选词规则处理后的分数及 CPU 参考对照，返回分数超限、实际选词分歧或提前停止三者中的首个异常，停因差异单列在终止边界。 |
| 7 | 张量与算子对齐 | `comparison/tensor/`，按冻结的采集清单核验两端证据及独立 CPU 参考 | 按样本、Token 位置、rank 和调用配对，核对形状、布局、类型及状态，交付 GPU、NPU 分别相对 CPU 参考的误差和未匹配调用。 |
| 8 | 服务链路的问题定界 | `diagnosis/service.py`，接入 `three-step-demarcation` | 从原始请求和渲染 Token ID 追到两次独立的 Completion、Chat 重放，交付最早可定位的环节及证据限制。 |
| 9 | 模型算子的问题定位 | `diagnosis/operator.py`，消费第 7 项回执，可用 `l1-norm-probe` 粗筛 | 沿调用顺序判断偏差来自上游输入还是本次输出，分别列出首个超限调用、最大误差调用和可供复现的疑点。 |
| 10 | 问题重现与原因确认 | `diagnosis/replay.py` | 记录恢复的输入、权重和缓存等必要状态、独立重放和单变量对照结果。原因经验证后给出修复建议。 |
| 11 | 精度对齐报告 | 精度阶段 `alignment/`，汇总测量、比较及按需取得的诊断回执 | 沿用阶段 manifest、台账和 handoff，逐层列出基线、目标、实际覆盖、结论和可复查证据。 |

## 5. Skills 设计要求与后续补充需求

### 5.1 组织与调用要求

保留“总入口 → 阶段 → 独立任务”三层结构，当前对应 **1 个总入口、3 个阶段、10 个任务，共 14 个 Skill**。按可独立验收的业务拆分任务，不把读取 JSON、计算摘要等内部操作另建成技能，也不将原规划每一行都机械对应为一个新 Skill。

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

以下条目保留原始需求及其在现行版本中的处理方式。已进入下一阶段开发计划的条目另列规划归属。

| 原始规划内容 | 当前处理 |
| --- | --- |
| SGLang / vLLM 框架选择 | 当前固定 vLLM / vLLM-Ascend，不提供通用选框架能力 |
| SGLang 模型迁移与框架注册 | 未纳入当前技能树及执行路线 |
| 模型、镜像与测试集下载 | 纳入资产准备，支持按契约复用并核验，不限于重新下载 |
| 模型支持度预检、vLLM 迁移与注册 | 纳入当前业务职责，按任务证据分别验收 |
| 数据集精度测试 | 普通评测默认 EvalScope、兼容 AISBench。客户任务质量比较现已接入 EvalScope |
| 标准三步走问题定界 | 三步法已可独立调用。质量评测后的自动定界仍属后续规划，见[开发顺序](#复用范围与开发顺序) |
| 逐层激活值诊断与 GPU L1 norm | 已有张量阶段按清单报告逐调用误差。按 L1 范数粗筛并定位算子根因列入后续“模型算子的问题定位”，实际可观察范围按模型和部署模式验收。 |
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

`verify-agent` 可在同一前向关联 msProbe 模块/API 张量和可选的 mindie-debugger 缓存快照。`verify-agent-plus` 的 Token 定向重跑已能按题关联原生评分与全词表预测，张量定向重跑沿同一题的 Token 首异常回执绑定第三遍前向，按共同清单采集模块/API 的张量。[Token 定向重跑契约](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/references/contract.md#token-定向重跑)

单次 decode 可按输出序号检查已适配文本层的状态。Qwen 第 3 层的 K/V 和第 0 层的 GDN 是回查示例。完整请求还可按 prompt 位置或 `forward_id` 查到无输出的 prefill 块。后端和模式覆盖以分项实测为准。[采集契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#从完整请求回查每次前向) · [采集记录](MSPROBE_RUNTIME_CAPTURE.md)

若要比对 GPU 与 NPU 上每一步的模块及受支持 API 的输入输出，建议用 msprobe 采集张量。`task=tensor` 保存采集范围内的真实张量，`statistics` 保存统计信息。固定提交 `7ae8857` 的接入说明列出 GPU 原生 vLLM 的 Eager 采集，以及 NPU vLLM-Ascend 的 Eager、ACLGraph 采集。GPU CUDA Graph 在该版本的支持范围之外，切到 Eager 后取得的数据只验证 Eager 路径。[msprobe 的 vLLM 支持表](https://github.com/Ascend/msprobe/blob/7ae8857f16ced36aca1a7138de21c09a4c6e9925/docs/zh/user_guide/dump/vllm_dump_instruct.md)

Eager 在每次前向中调用模块和 API。图重放则一次提交已经捕获的一段设备任务，每次都用本轮输入重新计算。要取得 NPU 每次重放的中间值，msprobe 的 `AclGraphDumper` 需在捕图前挂接，使采集操作随图执行，再按步归档张量。[ACLGraph 采集说明](https://github.com/Ascend/msprobe/blob/7ae8857f16ced36aca1a7138de21c09a4c6e9925/docs/zh/user_guide/dump/aclgraph_dump_instruct.md)

第三遍共同采集关闭 L2 和 mindie-debugger。若随后独立诊断某个 NPU 模块或调用，可用 mindie-debugger 检查状态或复现。各探针的采集边界如下表所示。[mindie-agent/debugger 固定提交 `831e03d`](https://github.com/mindie-agent/debugger/tree/831e03d64de24bda35a90f88e7b7dc8fe531e014)

| 采集位置 | 输入与输出 | 使用边界 |
| --- | --- | --- |
| [ACLGraph 模块输出](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/aclgraph.py#L328) | 在图捕获前挂接模型模块，重放时采集匹配模块输出中的首个张量 | 默认选择叶子模块，循环保留 8 个槽位，每个快照最多取前 16384 个元素并转为 FP32。读回快照需要设备到主机复制，vLLM Inspector 的步号读取也会同步 |
| [张量观察点](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/precision/npu_watch.py#L176) | 对指定张量记录统计值，出现 NaN、Inf 或越过阈值时锁存有界快照 | 调用方选择张量和观察位置。设备端环形缓冲区的记录需显式读回 |
| [模块调用前后对照](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/precision/lockstep_diff.py#L1) | 在模块调用前后记录输入、输出和可达状态，按模块、调用序号和 rank 对照基线 | 快照读取会同步设备。启用 healing 会用基线状态或输出替换现场值，适用于独立诊断 |
| [单次算子或函数调用](https://github.com/mindie-agent/debugger/blob/831e03d64de24bda35a90f88e7b7dc8fe531e014/mindie_debugger/precision/call_repro.py#L94) | 包装选定调用，保存实参、返回值、可达状态的前后快照及 RNG 信息，供复现 | 采集粒度由调用边界决定。融合 kernel 内部中间值需要单独的可观察点 |

首轮任务质量评测直接请求服务。对选中题目执行 Token 定向重跑时，HTTP 记录器将本题的 `X-Precision-Trace` 加入实际推理请求。调用方按序登记采集、执行原生评测、封存预测，再用同一请求的响应核对评分。[定向复测实现](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/scripts/precision/token_run/measurement.py)

全量采集的验收应先列明预期样本、rank、步和采集点，再逐项核对张量文件是否落盘，记录跳过、截断、覆盖和写入失败。固定 NPU Eager 请求已有模块/API 覆盖、逐 Token 关联及采集开关输出一致性的实测；GPU 侧、其他执行模式、逐层张量的评测样本归属及延迟/设备内存开销仍须独立验证。后续若使用环形缓冲区，还需在槽位复用前导出快照。

## 7. 当前迁入状态与验证边界

截至 2026-10-28，新仓保留完整 12 个嵌套 Skill，当前资源数量见 TESTING.md。支持文件位于总 Skill 的 `scripts/` 和 `references/`，导入、索引、资源归属、输出保护及不依赖构建清单的资源枚举/复制已通过本地回归。现行路径契约见[架构文档](ARCHITECTURE.md)，本文件不另维护重复清单。

客户改造已合入本地 dev，版本见[项目当前状态](PROJECT_BRIEF.md#当前状态)。EvalScope 客户流程交付三个 JSON，显式 AISBench 保留精度执行且不支持三文件交付。已实现的默认值与交付生成不再列为待开发项，剩余实机、宿主和专项验证见[路线图](ROADMAP.md)。

迁入前的插件开发包验收、Git 提交合并和归档记录不作为本仓的完成证明，也不代表本仓当前版本或 Git 状态。本仓后续验收应绑定实际代码版本、执行环境和本次证据，并在本仓文档中记录结果与未覆盖范围。

文件复制与文档核对不证明 CLI 可运行、宿主可发现、目标环境可用、模型功能通过或精度达标。当前 CLI 结论来自实际离线回归；后续仍按任务分别取得资产、运行载体、模型/NPU、精度及宿主证据。单端固定样本或旧实测不能外推全量数据、GPU/NPU 对齐或任意模型支持。

本文整理需求与范围，不授权自动开发范围外功能、连接服务器、安装环境或启动模型任务。运行请求、实现行为与本次证据出现差异时，明确记录差异，并在对应契约或架构说明中维护当前答案。
