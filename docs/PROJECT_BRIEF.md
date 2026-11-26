# 项目简介

本仓面向模型迁移、推理部署及基础设施工程师，维护模型资产准备、vLLM / vLLM-Ascend 迁移适配、部署功能验证和精度测试的一体化 Skills。目标是在明确的模型、软件版本、硬件、数据和运行约束下交付可追溯结果。

## 文档职责

| 来源 | 唯一负责的内容 |
| --- | --- |
| [README](../README.md) | 面向首次使用者的项目介绍、快速开始与文档导航 |
| [根规则](../AGENTS.md) | Codex 行为、Git 权限、阅读和工作路由 |
| [开发技能索引](DEVELOPMENT_SKILLS.md) | 仓库级工作流技能、依赖闭包、来源快照和团队使用方式 |
| [文件忽略配置](../.gitignore) | Git 文件忽略的具体匹配规则；维护约定见 [根规则](../AGENTS.md#gitignore-维护) |
| [功能规划汇总](MODEL_MIGRATION_FEATURE_PLAN_SUMMARY.md) | 原始需求、当前范围、排除项和业务完成条件 |
| [架构](ARCHITECTURE.md) | 三层组织、调用、资源归属和当前迁入限制 |
| [接口索引](INTERFACES.md) | 公开接口及其权威契约的位置、兼容性维护方式 |
| [代码质量](CODE_QUALITY.md) | 源码组织和增长约束的唯一权威 |
| [工作流程](WORKFLOWS.md) | 开发审批、业务依赖、失败隔离和任务连续性 |
| [验证](TESTING.md) | 检查命令、证据层次、当前基准和缺口 |
| [安全边界](SAFETY_POLICY.md) | 外部操作、资产、凭据及资源所有权 |
| [路线图](ROADMAP.md) | 已知缺口的处理顺序与验收出口，不构成实施授权 |

本文件提供快速定位，不复制功能规划的需求清单或各任务的请求字段。

团队开发工作流技能保存在 `.agents/skills/`，随仓库提供；模型迁移业务 Skills 保存在 `skills/vllm-model-migration/`。前者支持本仓开发和验证，后者是业务交付资源。结构审计使用仓库内脚本，团队成员无需具备作者个人技能目录。

## 使用场景与成功标准

支持完整部署与精度流程、仅准备、仅部署、已有服务精度测试、独立任务和历史分析。调用者先选择范围，已有资产与服务经核验后复用，不把单任务扩展为整条链路。

成功标准按所选任务判断：资产身份齐备、支持路线有据、适配/注册进入最终载体、真实权重功能通过、原生评测有效，以及适用的比较与目标结论。完整 `deployment_precision/run` 还需独立导出三个交付 JSON。测量有效但目标未达标时，应交付真实 `fail`，不追求把报告改成通过。

任务质量对齐使用 EvalScope 总分、分项和可选逐题成绩，结果写入客户回执的 `task_quality[]`。有精度意图且未提供 GPU 基线时，先按模型检索公开资料，资料不足则记录缺项并继续交付。对未达标分项选定的题目，单卡同步 Eager 文本贪心复测可从同一请求取得原生评分和逐步 Token 预测。若两端在同一前文出现预测差异，可在首个异常位置按调用比较 GPU/NPU 张量，并对具有等价实现的算子做 CPU 复核。[任务质量与 Token 复测契约](../skills/vllm-model-migration/references/task-quality.md#从未达标分项进入-token-预测对齐) · [张量取证与比较契约](../skills/vllm-model-migration/skills/vllm-precision-align/references/tensor.md)

本仓不承担 SGLang 迁移、框架自动选择、正式性能测试和优化、自动异常修复循环、重新量化、训练、多机调度或宿主插件发布。保留的旧文件契约不改变当前框架范围。

## 当前状态

2026-11-26，三阶段判定优化经 `f1c1180` 合入本地 `dev`。[客户入口离线验收](TESTING.md#三阶段精度结论与仅补张量的续接)覆盖 CPU 复算失败后的张量达标、超限和证据不足三个出口。此前 Qwen3.5-27B 已完成 NPU 单题重跑和复用验证，真实 GPU/NPU 联合验证仍待完成。[质量验收](TESTING.md#2026-11-25-任务质量对齐单题验收) · [张量验收](TESTING.md#2026-11-25-运行时张量与算子对齐)

截至 2026-11-26，`verify-agent` 可独立采集 msProbe 张量并通过 mindie-debugger 观察已适配层的缓存状态。普通注意力和 GDN 的单机同步 Eager 采集按所选物理卡数工作。Llama 和 Qwen 的单卡、双卡已有 NPU 实测，四卡和八卡已通过离线验证。单卡还分别验收了 Llama 和 Qwen 的 ACLGraph 文本请求，以及 DeepSeek-V2-Lite-Chat 的 Eager MLA 缓存。单卡同步文本批次已有双模型全程数值对照。异步批次已有 Llama 共享前向的请求归属和双模型单前向数值证据，双模型多请求全程联合验收待完成。[使用契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md) · [按模型和模式查看实测证据](MSPROBE_RUNTIME_CAPTURE.md)

2026-11-05，缺省镜像选择功能 `e6dc69e` 已通过 `1a21cf0` 合入本地 `dev`。客户未指定镜像时，准备阶段从官方 Quay 选版，客户入口将选中的标签和目标平台 digest 交给镜像任务。同一任务续接沿用冻结的选择。显式镜像和模板的用法见[缺省镜像选择](../skills/vllm-model-migration/references/customer-task.md#缺省镜像选择)。

缺省镜像流程已通过离线回归，目标主机上的真实拉取和缓存复用仍待验收。此前的空闲卡优先与共享 NPU 部署也仍待真实四卡共享启动、功能和精度验证。见[镜像验证记录](TESTING.md#2026-11-05-客户缺省镜像)和[共享部署记录](SHARED_NPU_IMPLEMENTATION.md)。

GSM8K 派生目录生成默认 main 配置并将 README 纳入摘要，default/main 选择绑定同一测试文件；原始数据和历史回执保留，原生加载证明范围见[布局验证](TESTING.md#2026-11-03-gsm8k-本地子集布局兼容)。物理卡、载体逻辑卡和进程索引分别记录，部署、精度复核及配置导出消费同一映射证据；完整设备映射链的真实910B验收仍pending；本次2、3号卡直接部署省略了映射探针，不能替代该验收，边界见[设备映射记录](DEVICE_MAPPING_IMPLEMENTATION.md)。

- 客户输入保留 model、environment、accuracy 三组；组内按字段支持缺失、null、整数 -1。模型名、容器名、设备候选池和数据选择按证据解析、冻结；明确 prompt 先于默认值合成，数据集保持名称序列并逐字段补值。
- 数据路径缺失时，Agent 查官方来源及有依据的默认本地位置，通过内部 default_local 先尝试读取；默认位置明确不存在或官方未提供默认位置时下载到客户指定路径，坏数据保留并报错。评测继续绑定准备回执路径；默认目录复用与下载分支的证据仍为离线回归，详见[默认目录契约](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-dataset-prepare/references/contract.md#指定路径缺失时的来源选择)。
- 客户 EvalScope 每次有效正式准备调用原版安装脚本，首次默认共享 venv；续接继承基础 Python/venv，成功后固定实际 EvalScope 版本，操作开关每次明确指定。配置冲突和实际漂移在安装前核验。AISBench 与资产 SDK 保持各自准备流程。
- 客户新部署通过公开只读观察生成原生模板输入，按模板能力关闭 thinking，功能与普通精度测试共用 8192 输出预算；部署容量纳入两者最大输入长度。三种功能接口仍须完整结束、真实输入用量一致并经语义复核，具体边界见[功能输入契约](../skills/vllm-model-migration/references/customer-observations.md#功能输入与生成预算)。
- 完整 EvalScope 流程要求部署功能、全部必需精度项及三个 JSON 的独立 ready 导出回执。性能启动前通过 check_evalscope_handoff.py 核验并显式传递 Python、venv、CLI、版本及本次 PATH；核验 ready 表示环境可交接，两端共用仍须实际进程证据。

当前离线证据及命令统一见[验证基准](TESTING.md#当前验证基准)。原服务器已验证镜像、catalog、上下文预算、已退出同名容器替换，以及 Qwen3.5-27B 复用服务和新建服务的文本/Chat/SSE功能；一项 C-Eval 子集取得有效评分。后续因服务收到停止信号而中断，具体发起者仍无法确认，完整客户结果为 blocked；不据此推导服务当前存活或全流程完成。历次职责与结构背景见[实现记录](CUSTOMER_WORKFLOW_IMPLEMENTATION.md)，近期修复的 RED-GREEN 和实测范围见验证文档；历史提交前状态不作为当前 Git 或部署状态。

2026-11-03晚间沿原run-id续跑后，2、3号卡直接部署的三类功能请求和一组性能基线完成；该次20:26收尾观察确认异步调度候选已退出、任务服务不可用。原Skill客户回执为blocked，该次精度未评分、三文件未发布，尚无最优配置或优化收益结论。当前服务器状态须在获准续跑时重新观察；原始回执、指标、资源身份及续接边界统一见[续跑记录](TESTING.md#2026-11-03-qwen35-27b-断点续跑与直接部署)。

全部必需精度项、三文件导出、其他模型/SDK/镜像组合、共享环境两端实测及宿主发现仍为待验收项，出口见[路线图](ROADMAP.md)。此前下游性能Manager的宿主/容器解释器路径限制仍未修复；本次直接使用宿主EvalScope的基线不证明该Manager已兼容。控制入口要求 Python 3.10+；本仓不安装驱动/CANN/Docker，不提供性能优化执行或全部依赖锁定。源码、离线验证、本地 Git 集成和所述实机证据分别判断。
