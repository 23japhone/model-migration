---
name: vllm-migration-prepare
description: 适用于为华为昇腾上的 vLLM-Ascend 统一准备资产、进行支持度分析前的资料准备或部署前的准备；需要编排模型、镜像、数据集任务，复核版本资料并交付阶段台账时使用。
---
# vLLM-Ascend 准备阶段

确定本次必需资产，调用已有准备任务，复核回执并汇总交接。下载、镜像加载、解包与内容核验归各任务；阶段不启动模型服务或评测。客户辅助入口 scripts/customer.py 按[安装契约](../../references/evalscope-installation.md)每次调用原版安装脚本准备共用 EvalScope；同时提供冻结镜像身份及 AISBench/资产 SDK 隔离环境能力。帮助/预览仅返回 not_run。

## 确定范围

| purpose              | 必需任务                     | 完成条件                                         |
| -------------------- | ---------------------------- | ------------------------------------------------ |
| assets               | 用户明确指定的必需任务       | 对应资产通过；若涉及镜像，还需版本资料复核       |
| precheck             | model，可用 metadata 或 full | 模型资料与目标版本资料齐备；不证明模型架构已支持 |
| deployment           | model 与 image               | 完整模型、目标 vLLM-Ascend 镜像及版本资料齐备    |
| deployment_precision | model、image 与 dataset      | 上述资产及原始数据、split 和样本选择齐备         |

已有服务只缺数据时，可选择 assets 的 dataset 任务；不强制重新准备模型或镜像。`required=false` 表示本次可选资产，失败仍列出，不能交付为成功资产；purpose 强制的必需任务不能被这个字段跳过。

## 工作流程

1. 阅读[阶段契约](references/contract.md)。明确实际执行主机、资产与证据位置、目标硬件、platform、vLLM/vLLM-Ascend 版本和目的。资产存储位置由用户指定，不能把开发模型或镜像版本当作通用默认值。
2. 完整阅读本次选中的 [model](skills/vllm-model-prepare/SKILL.md)、[image](skills/vllm-image-prepare/SKILL.md)、[dataset](skills/vllm-dataset-prepare/SKILL.md) 指令，再生成对应任务请求。任务仍可独立调用；阶段通过公开 CLI 和 JSON 结果交接，不导入任务内部实现。
3. 按[共用阶段请求模板](../../assets/prepare-request.example.json)指定任务请求路径。原 prepare.py 继续使用已有解释器，不自动安装；客户模式分别准备评测和资产解释器，本地完整权重不要求资产 SDK。客户 EvalScope 每次通过安装前核验的正式准备均调用随包安装脚本，首次默认共享 venv，续接继承选择并固定实际版本；不额外查询 latest，帮助/预览停止业务。资产 SDK 与 AISBench 仍先检查现有能力，按各自缺依赖流程准备任务环境，再用实际 Python 委托原任务。超时和不兼容保留诊断，不按缺依赖处理。目标环境需完整复制技能与共同静态资源并核对摘要，不能把开发机路径当作目标机路径。
4. 根据[版本与硬件资料](references/sources.md)，核对目标版本搭配、硬件适用条件及 CANN/驱动等软件栈要求。留存官方资料、访问日期、SHA256 和复核说明。资料结论来自调用方的实际阅读，脚本只能核对记录结构、文件摘要和目标一致性，不会自动理解网页或确认真实安装环境。
5. 先执行 `--plan`。它委托各任务检查参数、静态资源和证据路径，读取指令并记录资源摘要，不下载、不访问 Docker、不读模型或数据内容、不写运行文件；它不能证明资产已存在或版本资料已复核。
6. 去掉 `--plan` 后执行已授权范围。每次创建独立尝试，冻结任务输入并逐个调用任务；每个任务复核后立即更新台账。某个任务失败、阻塞或回执不可信时，继续其他独立任务。超时或取消时终止本次拥有的 CLI 进程并保留部分输出；取消后停止后续任务。Docker daemon 侧操作需另外复查。共享证据无法写入时停止新操作，不能宣告完成。不自动重试、回滚或删除资产。
7. Hugging Face、ModelScope、数据集来源或 registry 遇到网络问题时，先核对实际执行进程的网络、DNS、TLS、权限及代理状态，不能只凭超时认定代理故障。**确认是代理问题后，先询问用户是否有可用代理；有则请用户提供，并在实际执行进程或 daemon 中使用**，凭据不写请求、台账或日志。模型任务使用 HF 且用户没有可用代理时，按[模型来源切换规则](skills/vllm-model-prepare/references/sources.md#无代理时改用-modelscope)改用 ModelScope，不再等待 HF 代理或单独验收其网络可达性；用户已回答的情况不重复询问。生成独立模型请求和资产目录，在新阶段尝试中调用原任务，不修改执行中已冻结的请求或旧失败台账；其他已准备资产由各任务重新核验后复用。该模型规则不替代数据集 URL 或镜像 registry 的来源核验。Docker registry 核对实际 daemon 的网络配置，不能只配置 CLI 的代理；其他独立任务继续。
8. 阅读阶段清单、台账和交接，核对必需任务、实际内容身份、路径、host/daemon、版本资料、原始证据及未覆盖项。阶段拒绝与请求不一致的资产声明、缺失的样本选择和畸形回执；资料逐条复核，保留坏记录原因，已确认的版本 fail 不因其他记录损坏而降级。消费前重新调用相应任务核验已有资产；不能直接把旧阶段清单当作当前资产仍有效的证明。

```bash
python3 "/实际技能目录/scripts/prepare.py" \
  --request "/实际请求目录/prepare.json" \
  --work-root "/用户指定证据根" --run-id "prepare-001" --plan
# 核对后去掉 --plan，执行本次准备。
```

## 完成与交接

`status=completed`、`conclusions.assets=pass` 与 `conclusions.preparation=pass` 同时满足，才表示本次准备范围完成。资产齐备但必需版本资料待复核时，资产可以 pass，阶段仍为 blocked。明确资料冲突或必需任务失败时，阶段 failed。缺少必需请求、工具或无效任务输入时，阶段 blocked，不能用 skipped 隐藏缺口。

`version_materials=pass` 只表示已有官方资料复核记录齐备且摘要一致，**不代表实际 CANN/驱动已兼容或模型已支持**。原样保留子任务的 source、hardware_compatibility、runtime_support 等结论和未覆盖项；阶段 runtime_support 始终 not_evaluated。镜像缺少上游来源证明时，仍需后续复核，不能因资产身份已固定而提升来源结论。

交付包外 `prepare-manifest.json`、`task-ledger.json`、`prepare-handoff.json` 和子任务清单绝对路径。共同结果使用[组级 Schema](../../references/task-result.schema.json)，交接格式见[静态模板](../../assets/prepare-handoff.template.json)。阶段只交付后续预检、部署或评测需要的资料与资产，不执行这些后续工作。
