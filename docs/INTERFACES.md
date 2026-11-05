# 接口与兼容性

本文索引公开接口及其维护规则，不新建统一请求格式。字段、默认值、验证边界和产物格式以对应 Skill 的现行契约及源码为准；差异须明确记录，不靠文档替实现承诺。路径与资源基准见 [架构第 9 节](ARCHITECTURE.md#9-路径与资源契约)。

## 公开入口

下表脚本相对于对应 Skill 目录；每行链接指向权威契约。12 个主入口保留 --request --work-root --run-id 和 --plan；总入口另有互斥 --task。8 个辅助入口不计为新 Skill；resolve.py 使用 --model/--request；原版 install_evalscope.py 使用自身安装参数和 --dry-run/--help，其他参数按各自契约读取。

| Skill / 契约 | 公开脚本 | 操作或职责 |
| --- | --- | --- |
| [vllm-model-migration](../skills/vllm-model-migration/references/contract.md) | `scripts/migrate.py` | `--action run/analyze`；scope 为 prepare、deployment、deployment_precision、precision |
| [三文件交付](../skills/vllm-model-migration/references/tuning-handoff.md) | `scripts/export_handoff.py` | 独立核验并发布三个 JSON，--layout legacy/flat |
| [EvalScope 启动前核验](../skills/vllm-model-migration/references/evalscope-installation.md#性能启动前只读核验) | 总入口 `scripts/check_evalscope_handoff.py` | 同 run-id 的 customer_manifest 摘要引用；只读探测并输出本次 Python/venv/CLI/PATH，--plan 不读引用或写目录 |
| [vllm-migration-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/references/contract.md) | `scripts/prepare.py` | 准备阶段 |
| [客户输入及 Agent 发现](../skills/vllm-model-migration/references/customer-observations.md) | 准备阶段 `scripts/customer.py`；模型任务 `scripts/resolve.py`；verify-agent `scripts/customer.py`；verify-agent-plus `scripts/catalog.py` | 隔离环境/镜像解析、模型身份复核后复用、部署观察/精确替换、只读原生功能输入观察（functional_inputs）、原生数据目录；通过公开 CLI 委托 |
| [新部署容量观察与分配](../skills/vllm-model-migration/references/customer-observations.md#空闲优先与共享部署) | verify-agent `scripts/customer.py` | observe capacity=true 生成本次证据；allocate 可选 idle_first_shared，客户自动指定且必须绑定观察。verify 可选 npu.allocation 绑定预算及共享权限；无策略请求保持旧独占行为，独立无 observation 仅表示离线计算 |
| [EvalScope 共用环境安装](../skills/vllm-model-migration/references/evalscope-installation.md) | 总入口 scripts/install_evalscope.py；准备 scripts/customer.py 的 evalscope_environment 操作 | 六个参数通过内部 context 传递；客户续接继承选择、固定实际版本、安装前核验；帮助/预览独立记录、不进入业务 |
| [vllm-model-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-model-prepare/references/contract.md) | `scripts/prepare.py` | metadata/full 模型资产 |
| [vllm-image-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-image-prepare/references/contract.md) | `scripts/prepare.py` | 镜像获取与复用 |
| [vllm-dataset-prepare](../skills/vllm-model-migration/skills/vllm-migration-prepare/skills/vllm-dataset-prepare/references/contract.md) | `scripts/prepare.py` | 数据获取与选择；可选 default_local 先读官方默认位置，layout=evalscope 生成原生目录，详见契约 |
| [vllm-migration-execute](../skills/vllm-model-migration/skills/vllm-migration-execute/references/contract.md) | `scripts/execute.py` | `--action run/finalize` |
| [vllm-model-precheck](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/vllm-model-precheck/references/contract.md) | `scripts/precheck.py` | 静态支持候选和路线；客户新部署先转换镜像来源，再调用 `--plan` 校验请求，返回 `not_run` 不表示支持通过，见[转换与提前校验](../skills/vllm-model-migration/references/customer-observations.md#镜像来源转换与提前校验) |
| [adapt-agent](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/adapt-agent/reference/contract.md) | `scripts/adapt.py` | `--action capture/check`，不自动生成适配代码 |
| [vllm-model-register](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/vllm-model-register/references/contract.md) | `scripts/register.py`、`scripts/check.py` | 注册与只读载体复验 |
| [verify-agent](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/contract.md) | `scripts/verify.py` | `--action verify/review/stop/inspect-devices`；inspect-devices只读验证映射及全部设备进程归属 |
| [vllm-precision-align](../skills/vllm-model-migration/skills/vllm-precision-align/references/contract.md) | `scripts/precision.py` | `--action run/analyze` |
| [verify-agent-plus](../skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/references/contract.md) | `scripts/validator.py` | `--action run/analyze/preflight`；preflight 仅原生本地数据核验，兼容参数按契约约束 |

`--plan` 表示无业务执行效果的检查，但读取范围不同：例如总入口只读顶层形状，准备阶段还会委托子任务计划。不得将某一入口的计划行为推广到全部入口，不能从 `ready=true` 或退出 0 推断资产、服务或评测已有效。

两个历史精度 shell 文件保留兼容入口，不把它们作为新部署和评测的推荐路径。独立任务调用不等于单独复制子目录即可运行，仍须保留公共资源和实际依赖。

## 请求、结果与证据

共享 [task-result.schema.json](../skills/vllm-model-migration/references/task-result.schema.json) 定义 `schema_version`、`run_id`、`task_id`、`inputs`、`status`、`conclusions`、`evidence`、`uncovered`。它不是全部阶段 manifest、handoff、原生报告或内部台账的总 Schema，各产物仍依自身契约解释。

- 共享执行状态为 `not_run/running/completed/failed/blocked/skipped`；结论为 `pass/fail/not_evaluated/review_required`。内部台账可以有契约规定的其他状态，不机械套用共享枚举。
- 回执须核对 task/run/operation、原始输入、stdout 与落盘结果、文件引用及摘要；摘要固定字节身份，不证明执行可信、当前服务存活或模型能力。
- 原请求和已完成尝试不改写；绑定或续接形成新副本、摘要与独立尝试。资产路径属于实际执行主机或容器，不自动转换开发机路径。
- 总入口退出码为 0 完成且适用目标通过、1 执行/证据失败或目标未通过、2 输入/边界无效、3 必需条件不足。有效精度目标 `fail` 可以同时 `status=completed`、退出 1；子任务按自身契约解释，不能统一以退出码替代业务结论。

## 精度与交接兼容性

客户三组对象必须存在；组内字段按[客户默认契约](../skills/vllm-model-migration/references/customer-task.md#字段)支持缺失/null/整数 -1。输入结构、三个交付 JSON 不增加字段。静态默认归一化与动态发现分开：--plan 只列待解析项，实际模型名及全部 NPU 候选池绑定来源证据后才供消费者使用。显式数据选择不扩大，旧字段仍拒绝；客户默认标记不扩展到内部 evalscope_install 参数。

执行时先校验显式 JSON，再由 context 一次合成 prompt、应用静态默认；前置检查与业务执行共享实际参数。inputs 保留原归一化快照用于续接比较。--plan 不读取 context，仅作尚未合成 prompt 的 JSON 预览，其后端与默认参数不表示最终执行选择。

accuracy.datasets 的 prompt 仍为列表：两边均已选择时 name 序列须一致，条目内部 path/subset_list/limit 按明确值与未指定项合成；prompt 可省略未改字段，但不能省略已选名称。选择增删/重排或明确字段不同沿用集中冲突，非法结构沿用 invalid_input；不增加数组索引接口。完整语义以[数据集补值契约](../skills/vllm-model-migration/references/customer-task.md#prompt-补齐数据集字段)为准。

旧内部请求默认 EvalScope，显式 backend/version 优先，旧 .py 保留 AISBench 识别；省略版本或 latest 才查询稳定版本。客户 JSON 仍只含 model/environment/accuracy 三组及模板子字段。客户 EvalScope 每次有效正式准备调用共用安装脚本，续接继承基础 Python/venv 并固定首次成功版本，本次开关不继承；向精度传递实际 Python 与版本，不额外查询 latest。已有绑定先只读核验，实际变化不可先安装恢复。原版安装回执仅凭一致请求/结果/绑定恢复选择；证据不足和更早 selection 回执须新 run-id。历史 analyze 不联网重选版本、不重新评分。

保持原生指标、单位、数据选择、模板和生成条件。EvalScope 的正整数 limit 是每个所选 subset 的上限，省略或 null 表示所选范围全量。缺 GPU 不声明对齐，缺目标不声明达标；截断按现行任务契约记录，不能另加未经要求的零截断门槛。

完整部署和基线之后，还需独立导出 ready 及三文件路径/SHA256。客户 --task 自动完成候选生成和导出，--plan 不作交付证明。客户模式及显式 --layout flat 的路径为：

```text
work-root/run-id/deliverables/benchmark_plan.json
work-root/run-id/deliverables/accuracy_plan.json
work-root/run-id/deliverables/deployment.json
```

旧 export_handoff.py 默认 legacy，原布局保持兼容。客户负载由明确版本配方自动生成，不执行性能测试。AISBench 只执行精度，不能报告三文件完成。缺证据只阻塞交付，保留前序有效结果。

## 接口变更规则

变更公开参数、请求、状态、退出码或交接格式前，列出受影响调用者、原行为、新行为及兼容/迁移方案。批准的实现中同步源码、Skill 指令、权威契约、示例、相关资源规则、证据契约和行为验证；不能只改本文。禁止用新默认值补造历史证据或无声重解释旧字段。

本次路径修复保持公开 CLI、业务请求、退出码和业务版本标识；布局相对总 Skill 根，资源按当前目录与类型规则枚举，准备和注册保留实际摘要证据，详见总入口和准备阶段契约。旧运行回执不改写。
