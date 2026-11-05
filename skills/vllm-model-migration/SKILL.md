---
name: vllm-model-migration
description: 接收客户 prompt 与 task.json，完成 vLLM-Ascend 模型准备、迁移部署、精度基线及 benchmark_plan.json、accuracy_plan.json、deployment.json 交付。部署并精度测试、部署并性能调优的意图均适用；保留独立任务和历史分析。
---

# vLLM 模型迁移总入口

总入口负责范围、依赖和交接，任务实现仍归各阶段和任务所有。当前支持三个阶段的完整依赖链、显式续接和最终服务策略。 不将静态计划通过描述为部署、精度或整个流程已完成。

交付或搬迁时保留完整总 Skill 目录及嵌套的 `skills/`、`scripts/`、`references/` 和 `assets/`。路径支持从自身位置读取 `references/skill-layout.json`，按[静态资源路径契约](references/contract.md#静态资源路径契约)枚举当前资源并记录实际摘要，不需要构建或刷新资源清单。不依赖仓库目录、Git、调用时 cwd 或额外 PYTHONPATH；单独复制某个子 Skill 不构成完整运行资源。请求、资产和证据放在完整总 Skill 根之外。

EvalScope 输出达到已配置上限时，由 [verify-agent-plus](skills/vllm-precision-align/skills/verify-agent-plus/SKILL.md) 保留原生评分并记录截断，其他有效性检查通过即可完成该项评测；总入口不另加“零截断”门槛。原始基线与历史失败结论保持不变，不能用新规则补造旧流程的 completed 回执。

**完整流程的完成条件：** 部署验证、全部必需精度项有效、三个 JSON 校验通过，且独立导出回执为 ready。客户 `--task` 的 completed 包含这四项；旧 `--request` 的 completed 只表示阶段执行结束。文件契约见[三文件交付](references/tuning-handoff.md)。部分阶段、独立任务和历史分析不强制三文件。

文件交接按模型身份和声明部署配置关联基线：配置相同的重建服务视为同配置部署，容器名称、CID、PID、启动时间和回执路径变化不单独阻塞导出。两次运行的原始引用均保留；该关联不改变运行中的进程身份检查、服务所有权或历史结论。

## 客户 prompt + task.json

先阅读[客户字段与默认行为](references/customer-task.md)和[Agent 执行与续接](references/customer-observations.md)。客户只填写[template.json](assets/template.json)，不能要求客户填写阶段请求、评测命令或压测负载。model、environment、accuracy 三组对象必须存在，允许空对象；组内字段按契约支持缺失、null、整数 -1。空串、字符串 "-1"、布尔值和显式空列表不作为默认。模型名由实际来源/config 解析，设备默认是当前主机全部 NPU 候选池，datasets 整组未指定时为 ceval 和 gsm8k。旧字段仍拒绝；模板具体模型和设备值仅作示例。

prompt 对数据集内部补值仍使用 accuracy.datasets 列表：与 JSON 的名称序列一致，逐字段补齐 path/subset_list/limit；不修改的条目只写 name，明确值冲突集中确认。不新增索引路径、不合并子集列表、不改变显式数据集选择。完整规则与示例见[数据集补值](references/customer-task.md#prompt-补齐数据集字段)。

1. 理解 prompt，确定完整部署意图及显式条件。部署并精度、部署并性能、部署并精度与性能三种意图都执行完整流程。`model.model_name` 统一作为任务记录、结果标识、展示和 API 服务调用名，由它生成 vLLM `--served-model-name`、功能验证及精度请求的 API `model`。prompt/JSON 冲突集中询问；仓库 ID 与本地路径同时提供时，按来源证据核对身份，一致后复用完整本地权重，冲突时先确认，不自动覆盖或重新下载。
2. 用 `migrate.py --task ... --plan` 先做无外部效果的 JSON 校验；计划不读取 context、尚未合成 prompt，不能将预览后端当成最终选择。执行时先合成明确 prompt，再应用默认，环境边界与消费者共享同一结果。最终选择 AISBench 须在执行前说明本轮只支持精度执行，不支持三文件交付，不自动切换工具。
3. Agent 在包外生成 context，绑定 task.json 摘要、意图及实际证据。每次有效正式准备调用原版 install_evalscope.py；首次默认共享 /data/evalscope-venv，续接省略 evalscope_install 时继承环境并固定首次成功版本，操作开关不继承。已有绑定先只读核验，配置变更或实际漂移在安装前拦截，详见[安装契约](references/evalscope-installation.md)。安装帮助/预览独立记录并停止业务执行；正式环境阻塞仍继续独立资产步骤。使用各所有者的公开入口补齐模型、镜像、版本配方、硬件、原生数据与 token 长度，不要求客户补写内部配置。
4. 运行 `--task ... --context ...`；缺 Agent 观察时继续发现并用 `--resume 上次/customer-manifest.json` 续接。已解析版本、已执行配方和资产身份保持冻结；不重新选 latest。数据和部署的独立就绪步骤继续执行。新部署自动通过公开功能输入观察生成原生模板请求，将其真实长度纳入8192输出预算；详见[功能输入与生成预算](references/customer-observations.md#功能输入与生成预算)。

客户新部署默认空闲卡优先，逐卡剩余显存满足预算时允许共享占用卡；共享组合每张卡额外预留本次剩余显存的5%。容量不足形成用户更换卡决策，独立准备继续；选卡、两次启动前复核及运行归属规则见[共享部署契约](references/customer-observations.md#空闲优先与共享部署)。
5. 遇到适配或注册，读对应 Skill，完成公开流程并通过 context 补充交接。遇到 semantic_review_required，读实际响应执行 verify review → execute finalize，把最终迁移引用写入 context.migration_result 后续接；禁止脚本代判通过或重复启动服务。
6. 全部有效 EvalScope 基线完成后自动生成候选并整体发布。只交付 `work-root/run-id/deliverables/` 下三个 JSON；保留内部请求、原生分数和证据。导出失败续接导出，单项评测失败续接受影响项。服务默认 keep。
7. 后续性能启动前，调用 [check_evalscope_handoff.py](scripts/check_evalscope_handoff.py) 核验同一客户任务的环境；具体请求与调用约定见[启动前核验](references/evalscope-installation.md#性能启动前只读核验)。仅使用 ready 返回的 benchmark_python 和 env，以 argv 列表及子进程 env 启动获准的性能入口，保存实际解释器和 EvalScope 命令路径。阻塞时不启动性能，也不安装修复；本仓不代跑性能或修改性能插件。两端实际证据齐备才报告共用成功，版本绑定不等于全部依赖锁定。

```bash
python3 "/实际资源/skills/vllm-model-migration/scripts/migrate.py" \
  --task "/包外/task.json" --context "/包外/agent-context.json" \
  --work-root "/包外/证据根" --run-id customer-001
```

context、发现报告与回执是 Agent 工作文件，不是第三份客户输入。无 context 的实际执行返回 owner=agent 的下一步；Agent 不能在此处停止完整任务。同名容器明确已退出时，按[客户观察契约](references/customer-observations.md#同名容器替换)自动复核精确身份、非强制删除并继续重建，不再询问删除授权；运行中或未证实退出的容器保留精确身份授权要求。真实前提缺失、业务冲突或缺必要仓库身份形成具体阻塞/待确认项。

## 确定范围

先阅读[总入口契约](references/contract.md)。框架固定为 vLLM / vLLM-Ascend；不提供框架选择、性能调优、自动异常修复、量化、训练或多机调度。不把两个精度诊断任务作为前置依赖。性能目标可以原样记录为 not_evaluated，GPU L1 norm 不属于当前输入。

| 需求 | scope / action | 选择与输入 |
| --- | --- | --- |
| 仅资产准备 | prepare / run | [准备阶段](skills/vllm-migration-prepare/SKILL.md)，不启动服务 |
| 仅部署 | deployment / run | 模型镜像准备或已有准备交接 → [迁移阶段](skills/vllm-migration-execute/SKILL.md)，不强制测试集 |
| 部署并精度测试 | deployment_precision / run | 模型镜像准备 → 迁移；独立数据准备 → [精度阶段](skills/vllm-precision-align/SKILL.md) |
| 部署意图涉及性能调优 | 客户 --task 完整流程 | 完成准备、模型迁移、精度基线及三个 JSON；不执行性能优化 |
| 已有服务精度测试 | precision / run | 精度阶段核对服务与数据；可按显式输入补齐依赖，不重复部署已有服务 |
| 历史精度分析 | precision / analyze | 仅精度阶段 analyze，不准备、不部署、不推理、不停止服务 |

明确请求独立任务时，直接使用原任务入口：准备任务 [model](skills/vllm-migration-prepare/skills/vllm-model-prepare/SKILL.md)、[image](skills/vllm-migration-prepare/skills/vllm-image-prepare/SKILL.md)、[dataset](skills/vllm-migration-prepare/skills/vllm-dataset-prepare/SKILL.md)；迁移任务 [precheck](skills/vllm-migration-execute/skills/vllm-model-precheck/SKILL.md)、[adapt](skills/vllm-migration-execute/skills/adapt-agent/SKILL.md)、[register](skills/vllm-migration-execute/skills/vllm-model-register/SKILL.md)、[verify](skills/vllm-migration-execute/skills/verify-agent/SKILL.md)；精度任务 [verify-agent-plus](skills/vllm-precision-align/skills/verify-agent-plus/SKILL.md)。不将单任务强制扩展为完整链路，不另造同义入口。

## 精度工具与历史来源

客户模式默认 EvalScope，按[共用环境安装契约](references/evalscope-installation.md)每次准备调用安装脚本，绑定实际 Python 与版本；实际环境变化要求新 run-id。统一精度方案及原生核验不变。旧 --request 的省略 version/latest 行为、显式版本和已有环境前提保持原样。显式 AISBench 保留原精度流程，与 evalscope_install 同时提供时报告不适用。

EvalScope 的 `limit` 由用户选择：填写正整数 N 时，每个所选 subset 最多测前 N 题；未填写或为 null 时，评测所选 split/subset 全量，无需追问数量或填入示例中的小样本数。该值写入被引用的 EvalScope 配置，不新增顶层请求字段。数据准备按单个原文件/子集分别交接，使用相同上限；未限量时 selection 不填 limit，offset=0，不使用随机 seed。根据准备结果生成实际 sample_ids，不能按上限虚构不存在的题目。输入、实际执行和最终 accuracy_plan 的 limit 保持同一含义；导出固定字段用 `limit: null` 表示全量，不把历史分科题数配置自动改写成 null。

历史分析沿用[历史请求示例](assets/entry-analyze.example.json)，精度任务的 source 可以是原报告、evalscope-native-v1 或 aisbench-native-v1 清单，按来源工具和版本只读核验，不查询当前最新发布。缺失原始证据仍 blocked/review_required，不以新默认值补齐旧身份。总入口核对阶段 conditions 与原始任务一致，保留各工具指标、单位及未验证项；不同工具分数不直接作为相对基线。

## 旧内部请求与独立任务步骤

1. 明确范围、目标环境和已有产物，完整阅读所选阶段的 Skill 及公开契约。子请求仍由对应阶段定义，总入口不猜测镜像、设备、端口、模型名、评测配置或目标。
2. 从[仅准备](assets/entry-prepare.example.json)、[仅部署](assets/entry-deployment.example.json)、[完整流程](assets/entry-full.example.json)、[已有服务精度](assets/entry-precision.example.json)、[历史分析](assets/entry-analyze.example.json)或[迁移完成后继续](assets/entry-continue.example.json)选择示例。将实际请求放在包外，替换路径和 SHA256；全零摘要只是形状示例。
3. 运行公开 CLI 的 `--plan`，检查 scope、阶段列表、depends_on、missing_inputs、服务策略和未验证项。计划只读取顶层请求，检查输出安装边界，不读取子请求、旧结果、模型、数据、评测配置或服务；ready 只表示顶层静态输入齐备。
4. 对已授权的范围，去掉 `--plan` 执行。仅部署使用 purpose=deployment 的模型/镜像准备，迁移模板不能预填 preparation；脚本只在包外副本中绑定本次交接。复用准备结果也会通过原准备阶段重新核验。已有服务精度请求不能声明新 deployments；数据可由精度阶段按原契约准备。
5. 阅读 entry-manifest、终态台账、交接与中文摘要，并追溯阶段原始证据。有效评测目标 fail 可以同时 completed / 退出 1；阶段缺资料或待语义复核仍为 blocked。缺适配时由 Agent 阅读 adapt-agent 并完成其公开 check，再提交含 adaptation 的新迁移请求；语义待复核时阅读实际响应，使用 verify review → execute finalize，随后用 migration.result 提交新总入口请求，不重新部署。不要自动判定语义通过或循环修复。
6. scope=deployment_precision 的旧请求继续使用[导出请求示例](assets/handoff-request.example.json)，把实际候选提交 `scripts/export_handoff.py --request ... --work-root ... --run-id ...`。默认 legacy 布局，`--layout flat` 发布同级三个文件。客户模式由生成器自动提供负载，不向客户询问压测参数。仅独立 ready 回执证明已发布；不以空模板替代交付。

```bash
python3 "/实际资源/skills/vllm-model-migration/scripts/migrate.py" \
  --request "/包外/entry.request.json" --work-root "/包外/证据根" \
  --run-id entry-001 --action run --plan
```

所有引用和执行路径属于实际运行主机。目标主机运行完整资源副本，不增加 SSH 调度框架。控制入口使用 Python 3.10+ 标准库；客户准备能力可在获准任务隔离环境安装资产 SDK 和评测依赖，不安装驱动、CANN 或 Docker，不修改系统 Python。与维护后端或特定宿主无关。

## 运行边界与明确续接

Agent 负责意图、适配和语义复核；确定性脚本负责阶段委托、身份核验、台账和结果。新架构缺适配回执时返回明确下一步，按 adapt 的公开流程完成后再继续；语义待复核沿用 verify review → execute finalize，不重新部署。复用结果须校验原始证据并由原任务检查本次实际资产或服务，不以旧报告代替当前运行态。

独立准备任务及独立精度项由各阶段分别交付；某项失败时保留有效结果。完整流程分别调用模型/镜像 deployment 准备和数据 assets 准备；数据失败不阻止部署，部署失败不阻止数据准备。精度绑定只阻塞实际消费者，独立评测仍执行。取消或证据不可写时停止后续效果，保留部分产物。不自动重试、回滚、调参或循环修复。

模型服务生命周期唯一归 verify-agent；需要精度测试的服务先保持 keep，内层 stop 请求无效；顶层显式 service_retention=stop 在消费者结束后委托公开 stop，只作用于本次新迁移拥有的 owned 服务。borrowed 及从 migration.result 复用的服务始终保留，停止失败或归属不可核验时报告 unknown 和失败。总入口不直接操作容器、PID 或 NPU worker。

执行状态与验收结论分开：有效精度结果目标 fail 仍可 completed；缺 GPU 不宣布对齐、缺目标不宣布达标。交付真实验收、离线测试、未验证项及服务观察时间。静态计划、历史分析和包结构检查均不证明当前服务存活或模型能力。

完整流程以精度 evaluations 的 id 显式声明 bindings；deployment 只能绑定 migration，dataset 只能绑定 dataset_preparation。共享数据准备只选一个 dataset 任务；被注入字段必须在原评测请求及阶段条目中留空。原请求不被改写，冻结副本重新计算摘要。完整示例的 keep 可显式改为 stop；先完成所有消费者再收尾，历史分析禁止 stop。
