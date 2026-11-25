# 阶段请求、结果和资源契约

`scripts/precision.py` 使用 `--request`、`--work-root` 和 `--run-id`。`--action` 支持 `run`（默认）、`analyze`、`quality` 和 `token`，可加 `--plan` 做静态检查。阶段使用 Python 3.10+ 标准库，通过 Skill 布局定位并调用 `prepare.py`、`verify.py`、`validator.py` 和 `compare.py` 的公开入口。远程运行时由调用方在目标主机执行本 CLI。

## 输入

下表适用于普通 `run` 与 `analyze` 请求。另两种动作各有独立的[任务质量请求](#任务质量对齐)和 [Token 请求](#token-预测对齐)。

| 字段 | 契约 |
| --- | --- |
| schema_version | 固定1.0。 |
| evaluations | 非空数组；每项 id 唯一。普通项有 request `{path,sha256}`，可选 dataset/deployment 引用下表依赖；run 可选 result 引用相同输入的有效原 run。未就绪项可仅用 id/blocked_reason，保留阻塞且继续独立项 |
| datasets | 可选名称→数据准备请求引用；必须有实际评测消费者。由固定 `vllm-dataset-prepare` 公开CLI调用一次，可共享。 |
| deployments | 可选名称→部署请求引用；必须有实际评测消费者。由固定 `verify-agent` 公开CLI调用一次。service.retention须显式keep，不自动改变原策略。 |
| timeout_seconds | 每次实际子CLI时限，默认14400，1—86400秒；子任务plan上限60秒，其自身更细时限仍生效。 |

所有引用为规范绝对路径及64位小写SHA256，拒绝符号链接重定向、重复JSON键、非有限数字和未知顶层字段。请求及原始日志不得包含凭据。阶段诊断和CLI展示日志对常见秘密字段作基础脱敏，不保证自由文本秘密识别；冻结的原始请求及任务原文不能以脱敏改变其身份。

run中每个request指向精度任务完整请求，或仅缺对应依赖字段的模板。已有deployment/dataset不能同时被依赖覆盖；阶段只在运行副本中填入本次准备/部署的固定回执引用，模型、端点及其他测量条件不会改写。已有文件路径和摘要不能只凭名称替代。精度任务继续核验配置、服务、权重、实际样本和原始产物。

result 仅用于 run 续接：原报告须 completed/run、完整 inputs 与绑定后的请求相同，且测量有效；随后通过 validator.py --action analyze 只读核对原生产物，保留原始 run 引用及独立 revalidation 回执。不能用 result 绕过变化的样本、配置或服务。analyze 请求不接受 result，避免通过历史分析发起新运行。

run 在依赖准备完成后，先对全部就绪且需要新测量的 EvalScope 项分别调用 validator.py --action preflight，再开始有效项的测量。每项保存独立 preflight 引用；失败只阻塞对应项，其他就绪项继续。历史 result 复用及 AISBench 路径保持原行为。预检回执不含有效评分，不能冒充 evaluation 的正式 receipt；正式运行再次核验数据，预检不构成资产锁。

评测工具和版本选择由精度任务唯一负责：新 JSON/YAML 配置未指定 backend 时默认 EvalScope，旧 Python 配置未指定 backend 时兼容 AISBench；显式 backend 按对应契约校验。新请求省略 evaluator.version 或设为 latest，运行时核验官方最新稳定版与实际安装环境，版本落后、索引不可用或配置冲突时阻塞，不自动安装、升级或回退。旧显式版本请求保留精确期望语义。阶段仅填入显式依赖交接，原样传递所有 evaluator 字段，不把 latest 写成实际版本。--plan 不联网核验版本。

analyze中request指向精度任务的分析请求（source及可选历史baseline/targets）。不得声明datasets/deployments，不发起准备、部署或基线新推理。缺少原始证据的历史结果不能被阶段声明为有效。支持旧报告、evalscope-native-v1 和 aisbench-native-v1；格式放在被 source 引用的原生导入清单中，不作为阶段顶层字段。原生产物导入由精度任务按其公开契约处理，根据来源实际工具/版本分析，不查询最新发布、不加载评测环境。失败来源不提升为成功，缺失配置身份继续 review_required。

--plan只读取顶层请求并检查形状、引用写法和完整安装资源输出边界，不读取引用文件、不生成子任务计划、不执行Python评测配置、不访问服务、不写运行目录。实际运行才读取固定引用及已有交接的资产根，再检查输出隔离；因此计划通过不代表资产、环境或服务可用。

source、deployment、dataset、两种原生格式的 config_index 及baseline.report指向独立来源尝试目录，整个父目录受保护；即使报告缺失、损坏或摘要不符，也不向该目录写新尝试。原生清单缺少 config_index 或 format 类型错误时，仍收集声明的索引和其他可读取引用；一条必需引用失败不截断其他资产边界。请求文件本身可以集中存放，但不能将输出放进声明的来源尝试目录；可选基线不可用仍可在其他独立目录完成单端测量。

资产扫描识别明确的路径字段和protected_roots/weight_files路径列表；普通答案、stop或其他内容列表里的 `/` 等文本不会被当成文件系统根。

## 执行和回执

显式准备/部署请求在前置输入有效且仍有消费者时调用；每个独立依赖失败只阻塞其消费者。每次依赖结束重新计算就绪状态；数据准备失败后，不启动只服务于这些失败消费者的部署，共享部署仍为其他就绪消费者执行。无效部署输入不会触发无其他消费者的数据准备。部署语义review_required时保留原报告，由调用方按部署任务的公开review步骤取得完整交接后新建阶段请求；阶段不自动审阅、部署重试或循环修复。已有服务复用时不重复部署。

每个子任务先执行公开plan，然后冻结规范化请求并执行。数据准备的默认值和路径规范化由它自身负责。阶段核对schema/task/run_id/inputs、实际operation、终态、退出码及回执必须属于该次独立子任务目录；stdout中声明的共同字段与持久结果一致。精度回执的 detailed_report 必须为对象，measurements 必须为数组，每条记录必须有布尔 valid 和对象 metrics；completed 必须有非空有效测量和指标，conditions 只能为对象或 null。畸形结构或 completed/pass 与实际空/无效测量矛盾时，仅拒绝该项回执并继续独立项，不在阶段重新评分。精度任务在写attempt前返回的blocked结果独立保存为inline回执，只证明阻塞，不视为测量。

run下可选GPU基线读取失败不阻止单端评测，具体比较限制由精度任务报告。没有目标不宣布达标，目标未达标不会调用诊断、修改生成条件或自动重测。results 每项的 conditions 原样保存精度任务实际评测条件，包括已有的 evaluator_backend、evaluator_version、evaluator_resolution 和依赖/源码身份；旧来源缺字段时不补造，缺条件时为 null。条件与版本核验、可比性和目标判定仍归精度任务。实际分数、指标名及单位从精度任务原样交付；阶段不重新评分，不要求完整回答逐字相同。

## 结果和退出码

包外 `work-root/run-id/vllm-precision-align/UUID/` 包含：

- precision-stage-manifest.json：共同schema_version=1.0、task_id、run_id、operation、inputs、status、success、conclusions、evidence及uncovered；另有tasks/results/resources/errors和next_action。
- task-ledger.json：datasets、deployments、evaluations各项状态、原因、回执及结论。终态不保留running/not_run；取消前未启动项为cancelled。
- precision-stage-handoff.json：固定最终台账SHA256、tasks、逐项报告、资源交接和结论；manifest再固定该handoff的摘要。
- 原始冻结请求、每次子CLI的plan/执行stdout、stderr、argv、cwd、PID、退出码和超时/取消信息，以及children下的完整原生产物。

阶段status和目标分开：所有必需评测有效结束时completed，精度目标可以fail或not_evaluated；独立执行失败为failed，缺交接或待复核为blocked。单项有效结果即使阶段失败也保留。conclusions分别为precision_testing、evaluation_validity、comparability和precision_target。只有实际请求的全部目标pass才能宣称整体目标通过；未请求目标的项不取消其他明确目标。阶段success要求completed、全部评测有效、所有已请求目标pass。

退出0表示阶段有效完成且所有适用目标通过或未请求目标；1表示执行/证据失败、取消、超时或目标未通过（状态仍可completed）；2表示顶层输入或输出边界无效；3表示必需输入、交接或语义复核不足。不能只凭退出码判断精度是否有效或是否完成。

终态发布先固定台账，再写绑定该台账的handoff，最后manifest；发布handoff后不改台账。独立attempt不覆盖旧尝试，重新run会再次执行对应任务，不承诺自动恢复或exactly-once。SHA256是内容固定依据，不是签名或可信执行证明。基础磁盘错误时不能保证终态落盘，必须明确失败并停止后续效果。

## 资源和取消

服务启停和精确身份归verify-agent，阶段始终不直接停止容器、PID或NPU worker。资源索引引用原始部署报告及完整handoff，注明owned/borrowed、生命周期所有者和部署时观察；实际当前服务/worker核验由精度任务完成。analyze不刷新服务运行态。

取消/超时先向本次自有CLI发送SIGTERM并等待30秒收敛；仍未退出才结束该自有进程组，记录resource_cleanup_unconfirmed，不宣称服务已释放。取消停止整个本次阶段，保留已完成项；超时作为该项失败，继续其他独立就绪项。部署已经产生的外部效果必须阅读原所有者交接，阶段不自动回滚或重跑。

## 任务质量对齐

使用 `scripts/precision.py --action quality --request <阶段请求> --work-root <证据根> --run-id <标识>` 单独处理一份客户基线。客户总入口为 `accuracy.gpu_baselines[]` 的每个条目生成该请求。以下字段属于包外阶段接口。

| 字段 | 用途 |
| --- | --- |
| `schema_version` | 字符串 `1.0` |
| `baseline` | 客户填报的 EvalScope 数据集分数、目标和可选逐题评分 |
| `native_request` | 交给 `verify-agent-plus` 的完整冻结测量请求，包含已绑定的部署、数据、配置和期望协议 |
| `candidates` | 可选，原 NPU 测量回执的 `{path, sha256}` 数组。省略或空数组表示没有历史候选 |
| `gpu_evidence` | 可选，经 Agent 搜集的 GPU 历史记录的 `{path, sha256}` 引用 |
| `require_reuse` | 可选，默认 false。第二次单题验收可设为 true，找不到合格候选时保留失败原因并结束，以保证零新增正式请求 |
| `timeout_seconds` | 可选，每次子任务超时秒数，默认 14400 |

按顺序检查候选回执，调用原生 validator 的 `--action analyze` 复核可读候选。首个通过核验且覆盖所需分数的候选标记为 `reused`，保留原回执及本次复核回执。没有合格候选时，默认发起一次 `quality-run`。`require_reuse=true` 时记录失败原因并结束。取得测量后，由比较任务输出质量报告。显式填报的 GPU 历史条件无法核实时，仍保留有效的 NPU 成绩。

阶段终态写入 `<work-root>/<run-id>/vllm-precision-align/quality/<attempt>/quality-stage-manifest.json`。取得测量后，`receipt` 指向原始回执，`measurement` 指向原生评分，`quality_report` 指向比较报告。复用时 `verification` 指向本次只读复核回执，重跑时与 `receipt` 相同。候选选择依据和耗时见 `candidate_checks`。阶段的 `completed` 表示已交付报告。读取报告的 `conclusions.precision_target` 判断目标是否达标，通过 `scores[]` 查看分项成绩和可选逐题变化。客户回执的 `task_quality[]` 给出阶段状态与可用报告的位置，三个交付 JSON 按原有条件导出。

质量重跑由 EvalScope 直连现有服务，预检在客户端计算提示长度。比较使用原生报告、题目身份和评分，依靠配置、版本及摘要核对口径。质量任务本身不启动 HTTP 正文记录器，也不启用 Token 预测数据、模型层或算子张量采集。若可比的评分项未达标，后续诊断可针对这些样本另行重跑并采集所需数据。

客户精度意图明确且 `accuracy.gpu_baselines` 为空时，客户入口应先按顶层模型名称与仓库身份查找公开评测证据。模型卡上的展示分数可作为搜索线索。取得可核验的 GPU 原生记录、实际权重和评测口径后，对照本次编译的条件再安排比较。搜索无结果、身份歧义或准备时发现条件缺失时，客户回执在 `gpu_baseline_search` 保存搜索记录，在 `task_quality[]` 记录 `not_evaluated`、缺项和 `quality_measurement_started=false`。已进入阶段后复核出静态条件不符时，客户条目标记 `not_evaluated`，其 `report` 引用的报告记录原因、`quality_measurement_started=false` 与零次正式请求。两种情况均继续后续任务。

## Token 预测对齐

通过 `precision.py --action token --request <请求> --work-root <包外目录> --run-id <标识>` 处理一份已发布的任务质量报告。请求提供 `schema_version="1.0"`、`quality_report={path,sha256}` 和 `token_alignment`。其中 `raw_logits.atol` 用于原始 logits，`processed_scores.atol` 用于选词前的归一化概率，两者均为非负绝对容差。可选 `bundles` 为预测包引用数组。仅当需委托 NPU 定向评测时提供首轮相同协议的 `native_request`。加 `--plan` 只校验参数形状和输出边界。

阶段对照 `next_action.failed_scores` 和 `scores` 的可比未达标分项，按指标方向合并首轮报告和已核验原生 reviews 中的退化题。总分和学科分数指向同一题时仅列一次，保留触发它的每个指标。只有学科分数时，可以从首轮有效 NPU 回执取得该学科完整题号名册。仅有总分且无法定位样本，或缺少可核验的学科名册时，交付待定界范围。`selection.json` 的 `cases` 给出子集、原题号和触发项，`scope_coverage` 区分已定位的退化题与完整学科候选，`pending_scopes` 记录缺失依据。

一个预测包记录 `format=token-prediction-bundle-v1`、设备 `hardware=gpu|npu`、`quality_report_sha256`、`evidence_kind=synthetic|runtime` 和 `cases`。每题提供 `subset`、`sample_id`、指向预测记录的 `record={path,sha256}`，可附 `cpu` 和 `review` 引用。预测记录及 CPU 核验字段见[比较任务](../skills/vllm-precision-compare/SKILL.md)。第二遍逐题评分文件含相同题号、子集、设备、来源类型、质量报告摘要、`record_sha256` 和 `metrics` 原生指标分值。具有原始退化证据的题可据此判断是否复现；整科候选只报告第二遍实际观察到的分差。模拟结果均标明来源。

有 GPU 对照、该题缺少 NPU 记录且传入与首轮相同的 `native_request.protocol` 时，阶段将这些题目委托给 `validator.py --action token-run`。已有双端记录的题目直接比较。子任务部分成功时，先核对成功与失败清单互斥且覆盖全部受托题目，再导入成功项、报告每项失败原因。每题经 `compare.py --action token` 判断，首个异常 Token 从 1 计数。存在显式 CPU 数据时使用该引用，也可从首轮已固定的本地模型和部署交接生成 CPU 参考。缺失 CPU 依据单独记录。原生质量、第二遍评分复现及 Token 数值结论各自交付。

每次运行在 `<work-root>/<run-id>/vllm-precision-align/token/<attempt>/` 写入阶段回执 `token-stage-manifest.json`、选题清单 `selection.json` 和一份汇总报告 `token-report.json`。阶段摘要公开 `status`、`alignment_status`、`selection`、`selected_count`、`token_report` 和 `manifest`。打开 `token_report` 引用，按 `cases[].subset` 和 `sample_id` 找到题目，读取 `result`、`first_anomalous_token`。有单题比较明细时，`cases[].comparison` 指向该题的 `case-<序号>/comparison.json`。

首轮目标未失败时为 `not_triggered`，有模拟来源时查看逐题结果并区分 `simulated`，依赖缺失时报告 `insufficient_evidence` 或 `partially_evaluated`。`comparison_scope=provided_predictions` 表示所有数值结论只覆盖清单中实际成功比较的位置。外部预测包的 `runtime` 标签由提供方声明。通过本阶段执行的 NPU 采集另有 `npu_measurement` 原生测量回执可核验。历史质量回执保持只读。

客户按同一任务及 run-id 续接、补入与首轮质量报告摘要绑定的预测包时，本次质量阶段仍复核当前候选。若原始 NPU 回执、GPU 来源、测量协议、指标和质量判定均一致，`task_quality[].report` 沿用冻结报告，`stage` 指向其原始阶段，`verification_stage` 引用本次复核阶段。候选或条件变化时使用本次新报告。
