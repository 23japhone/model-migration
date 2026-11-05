# 请求、产物和验收契约

公开入口为 `scripts/validator.py`；控制脚本使用 Python3.10+ 标准库，评测解释器独立指定。统一参数为 `--request --work-root --run-id --action run|analyze|preflight --plan`。JSON 重复键、未知字段、非有限数字、非法路径及输出越界在运行前拒绝。所有路径属于执行主机，使用规范绝对路径；`{path,sha256}` 固定原始文件字节。

## 无推理数据预检

`--action preflight` 接受同一 run 请求，本轮仅支持 EvalScope。读取部署文件交接但不探测模型服务、不读取权重、不推理、不评分、不安装。数据清单及配置重新核验，在指定解释器使用原生 loader、record_to_sample 和 prompt 逐条检查科目、split、数量、顺序和内容；缓存写入独立尝试。显式版本检查实际安装；省略版本/latest 仅记录当前安装版本并标记 latest_checked=false，正式 run 的版本策略不变。预检不会执行 baseline.request 或判定 targets。

成功返回 completed/success=true、退出0，详细结果为 detailed_report.data_preflight；evaluation_validity/comparability/precision_target 全部保持 not_evaluated，不能作为历史测量或交付基线。数据/配置失败返回 blocked/退出3，保留具体路径、子集与错误码；预检取消时保存 failed/退出1及 dataset_preflight_cancelled，不保留 running 状态，也不产生精度结论。--plan 仍仅检查形状与输出位置。正式 run 自动在服务观察之前执行相同预检，并在配置冻结、测量后重新核验资产；旧 --request 同样经过此门禁。

未填写 local_path 时由准备回执 asset.path 绑定到运行副本；已填写时保留并校验，越界或冲突返回 dataset_path_conflict（含配置路径、验收路径和修正建议），不覆盖原始配置。C-Eval 缺目录元数据为 dataset_metadata_missing；额外文件为 dataset_unlisted_file；数量、顺序或题目答案不一致为 dataset_sample_mismatch。原始配置另行归档，最终冻结配置包含实际绝对路径。

进度 phase=checking_dataset/dataset_ready 分别表示数据预检中/数据就绪；任务 ledger 的 starting_evaluator 仅表示正在启动评测器。其 progress 指向 http-capture/progress.json，只有真实推理 POST 已转发才写入 phase=evaluating；不能根据 PID、进程 running 或预检完成推测已有推理或评分。

## run 请求

| 字段 | 约定 |
| --- | --- |
| schema_version | 固定 `1.0`。 |
| deployment | 已通过功能验收的 `verification-manifest.json` 引用。要求 completed、success=true 和七类必需结论 pass；review 报告必须绑定原始来源及相同部署交接。读取源交接真实模型、权重、运行版本、CID/PID、worker 和端点，不把文件路径当作运行证明。 |
| dataset | `vllm-dataset-prepare` 的 completed/assets=pass 清单引用。重新核对 asset 文件、大小、摘要、内容身份、原始选择和选择摘要，不下载数据。 |
| evaluator | `python` 为评测解释器绝对路径，`config` 为必需引用；可选 `backend=evalscope/aisbench`。EvalScope 使用 JSON/YAML TaskConfig，AISBench 保留 Python 配置。新请求省略 `version`，与 `version=latest` 等价；旧显式版本仅作精确安装期望值。可选 `timeout_seconds`，默认14400，范围1—86400，用于单个实际评测进程。配置探测独立限时60秒，其中 latest 查询超时15秒。探测与实际评测的 cwd 均为各自的尝试子目录；配置辅助文件须使用绝对路径或已安装且可导入的模块，不依赖调用方 cwd。 |
| tasks | 非空数组，每项有 `dataset`（逻辑任务标识；AISBench 仍为实际 dataset abbr）、`model_abbr`、`sample_ids`（实际 reader 输出的局部零基id）、`metrics`。任务标识唯一，不从名称猜测数据或评分器。EvalScope 还须有 benchmark、subset，并通过 model_abbr 对应 TaskConfig.model_id；其实际请求模型仍为交接的 served-model-name。AISBench 可选 source_columns 将实际 reader 列名映射到原文件的键、CSV 列号或嵌套键路径；省略时使用相同键名。映射只用于只读身份核验，不改写输入或评分。 |
| metrics | AISBench 为指标名到 `{unit,direction,path?}` 的映射；EvalScope 使用 `{unit,direction,selector:{name,aggregation,dimensions}}`，要求完整身份唯一匹配，不使用数组位置猜测。unit 为 `percent/ratio/value`，direction 为 `higher/lower`；path 为可选的原生 JSON 逐层键数组，省略时读取同名键。可用安全指标别名和 path 读取含特殊符号的原生指标，保留原值及取值路径。 |
| targets | 可选数组；每项 `{dataset,metric,kind,value,unit}`。必须预先声明，不根据结果选阈值。 |
| baseline | 可选，唯一指定 `report` 或 `request` 引用。report 为已完成的本 Skill 精度报告；原生评测资料先通过 analyze 导入。request 为独立完整测量请求，不允许递归基线。 |

## 工具选择与 EvalScope 配置

显式 backend 优先；没有 backend 且 config.path 后缀为 `.py` 时按旧 AISBench 契约识别，其他请求默认 EvalScope；选定工具后仍须通过对应任务字段与配置校验。运行默认从官方 PyPI 的 `evalscope` 或 `ais-bench-benchmark` 发布索引选择最高的非预发布、非开发且至少一个文件未撤回的稳定版本。实际环境须与该版本一致；不因解释器较旧而退选老版本。解释器要求读取实际发行包的 Requires-Python，不沿用某个旧 release 的固定上限。latest 查询失败、版本落后、工具缺失或配置不兼容均阻塞，不自动安装或切换工具。旧请求的具体 version 保留精确期望值检查；没有固定支持版本白名单。

原始 inputs 保持调用者内容，实际探测到的工具、版本和选择来源写入 detailed_report.conditions 的 evaluator_backend/evaluator_version/evaluator_resolution。尝试目录的 evaluator-version.json 记录策略、实际版本、时间、查询地址和索引摘要；latest 查询原文保存为 evaluator-index.json，成功探测时两者纳入配置文件摘要。选择来源及最新查询时间不参与可比性；实际工具版本、源码和依赖仍必须一致。来源的版本字段必须是实际版本，并与已保存的工具依赖条目一致；不能把 latest 写成历史实际版本。历史 AISBench 条件只在内存中补充可推导的工具身份，不改写来源文件，不联网核验当前 latest。`--plan` 不加载环境、执行配置或查询版本。

客户 --task 的环境准备由准备公开入口每次调用原版安装脚本完成，见[安装契约](../../../../../references/evalscope-installation.md)。validator 使用安装回执的实际 Python 和明确版本，不另查 latest；安装 perf 帮助检查不代替实际 TaskConfig、数据适配器、tokenizer、服务与原生结果核验。旧 --request 和历史 analyze 语义不变。

EvalScope 要求 Native/openai_api、明确 model/model_id/api_url、dataset_args 的 subset_list/eval_split（local_path 可省略并从回执绑定）、可选 limit（正整数上限；省略或 null 为全量）、judge.strategy=rule、model_args.max_retries=0，以及 generation_config 中显式 stream=false、n=1、retries=1、正整数 max_tokens。配置校验不静默改动生成参数；拒绝未知 TaskConfig/dataset_args 字段，以及原生 OpenAI 接口不生效的 generation_config 字段。示例中的参数由实际任务确认。SDK 由指定解释器的隔离模式加载，不使用调用方 PYTHONPATH 或用户 site-packages。新测量禁止 use_cache、rerun_review、重复生成和 ignore_errors；本轮不支持 AgentLoop、sandbox、LLM judge、多模态或自定义推理主流程。

local_path 应指向原生 loader 可加载的本地数据目录；直接文件是否可用取决于该 benchmark 的原生 loader，探测失败时不擅自更换 loader。通过原生标准文本 DataAdapter 加载本地数据，使用原生 record_to_sample 和 prompt 处理验证实际选择，不自行转换题目或评分答案。limit 由用户输入，正整数 N 对应每个所选 subset 原文件的前 min(N, 行数) 条；省略或 null 对应所选 split/subset 全量。0、负数、布尔值、字符串和小数拒绝。SDK 配置构造时显式传入用户上限或 None，防止依赖 SDK 的隐式限量默认值；原始配置不改写。数据准备按单个原文件/子集分别交接，selection 与 sample_ids 从实际选中记录生成，不能用 N 代替实际题数；多科全量可以题数不同但 limit 均为 null。其他行范围须由数据准备任务交接独立文件；这不会改动 AISBench 的切片支持。可保留基准原生 few-shot 配置，相关数据同样须在已交接本地数据根内。记录生效 TaskConfig、benchmark 元数据及 evaluation_version、冻结样本、环境依赖和实际模块源码摘要。原生 ChatMessage 的随机 id 不参与内容身份，prompt、参考答案及其余样本字段均核对。每个 benchmark/subset 独立执行，运行副本只隔离对应任务、输出/缓存目录和记录端点。

原生 predictions 和 reviews 使用 index；逐行检查覆盖、推理结果、score.status/value、评分参考及预测内容。schema_version=2 的原生 report 检查 execution_summary、subset 覆盖及指标 identity/semantics；该内部版本不改变共同外层 1.0。原生 raw_unit 非空时直接采用；为空时按官方 semantic_id 的 domain.concept.unit 取单位，并记录 unit_source，原始 semantics 保持不变。缺少明确原始单位时该指标无效，不能按 value 接受。原始 ratio 不因显示乘100而变为 percent；请求单位不符则不判定。只选择用户声明的原生指标，不借主指标缺失回退到列表第一项。原生报告可能把逐样本 acc 规范为 accuracy，selector 必须写报告 identity，不能用评分器内部名称代替。dimensions 按原生规则将整数浮点数归一化，布尔值仍与数字区分。HTTP、原生 messages、冻结样本和 reviews target 必须对应，reasoning 只用于按已核对原生格式核验证据，不改写送交评分器的内容。

## AISBench 配置

AISBench 配置须含一个明确的服务模型，实际 `model` 匹配 served-model-name，host/port 或服务 base URL 匹配交接（url 不再附加 /v1/chat/completions 等路由）；dataset abbr 和 model abbr 必须与 tasks 一致。支持非流式、每样本单次生成；显式配置 retry=1（此版本表示总尝试次数），不自动修正零次或多次尝试。实际 reader 的 split、切片、数量及输入/答案列内容必须逐行与准备选择一致；当前只处理单个原文件的明确切片。支持交接声明的 JSONL、JSON 对象列表及 CSV；CSV 表头约定沿用准备请求。无法证明列内容映射或 loader 已做无法对应的变换时阻塞，不凭数量相同宣布样本一致。实际数据路径必须在已固定的数据根内，不改写题目、模板、答案、预处理或评分器。

每个数据任务冻结完整实际配置，分别调用 AISBench `--mode all --num-warmups 0 --dump-eval-details`，关闭额外 warmup，以便逐项核对声明样本与 HTTP 次数；不默认合并子数据集，不启用性能模式。子任务命令仅增加路径引号适配，覆盖解释器、任务脚本及参数文件路径；AISBench 的评分和生成实现不变。探测、主 CLI 及子任务均使用独立解释器的 -I -B，忽略调用方 PYTHONPATH 和用户 site；辅助模块须安装于该评测环境。原始评测配置不被覆盖；执行副本保留原有 infer/eval/summarizer，只将服务连接转到本次自有回环记录器，并指定本次输出根。记录器逐字节透传实际正文，原始服务及生成参数不变。当前不支持认证及流式记录，配置含这些能力时明确拒绝。

## 基线和目标

- 绝对目标 `kind=absolute`：higher 指标要求测量值大于等于目标，lower 要求小于等于目标。
- 下降量目标 `kind=max_drop`：higher 使用 baseline-current，lower 使用 current-baseline，要求不超过阈值。`unit=percent` 表示百分点，不表示相对下降率。
- 相对下降率 `kind=max_relative_drop`：以上下降量除以基线绝对值，再乘100；unit 必须为 percent，零基线不判定该目标。
- 比较要求同一工具、工具版本、benchmark evaluation_version、指标身份/语义及实际权重、数据身份、原始样本选择、模板、reader、评分配置、生成设置和已采集的服务生成默认设置一致。指标单位、方向、原生取值路径和覆盖均须有效。还比较真实请求正文的规范化摘要（仅排除两端允许不同的 model 名称）；实际 prompt、few-shot 内容或生成正文参数不一致时不能比较。评测环境记录实际解析的完整 distribution 版本和 Python 版本；配置引用的模块源码也固定并在测量后复查。实际服务版本与交接核对，不因两端都名为同一模型就认为可比。
- 缺 GPU 基线不阻塞 NPU 单端测量，绝对目标可独立判定。缺目标不声明达标；相对目标缺比较条件时为 not_evaluated。有效测试可完成并报告目标 fail，不进入诊断、调参或重测。

既有 GPU 端点评测通过 baseline.request 使用独立请求。其 deployment 可为 `{schema_version:"1.0",format:"gpu-baseline-service-v1",handoff:{...}}` 的固定文件引用，仅在显式基线分支接受；不能代替主 NPU 部署交接。handoff 包含与部署交接相同的 model/runtime/resource/endpoint/final_service，以及 workers.listener_identities、workers.gpu_processes（pid 及完整 PID/start_ticks/pgid）。核对实际权重、服务模型卡、监听归属、资源身份及 nvidia-smi 计算 PID。该接口的存在不表示已有真实 GPU 验收。

## analyze 请求

`{source:{path,sha256},baseline?,targets?}`。source 通常为本 Skill 的 precision-manifest；重新读取已固定配置、原生预测、评分和 HTTP 证据，并保留原始执行有效性。分析不依赖仍在运行的模型服务或仍然存在的模型权重，不重新发送请求。analyze 的 baseline 仅接受已有 report，不接受会触发测量的 request。

历史来源依据自身工具选择 reader，不受当前默认工具影响。EvalScope 与 AISBench 不直接互作相对基线；绝对目标仍可按各自有效指标判定。

外部原生产物可使用固定的导入索引：`{schema_version:"1.0",format:"aisbench-native-v1",config_index:{path,sha256},tasks,files,provenance}`。EvalScope 使用相同外层并将 format 设为 `evalscope-native-v1`；tasks 带 backend、benchmark、subset 和 sample_snapshot。config_index 保存对应实际配置探测的 conditions/tasks/files；tasks 必须相同，files 为完整绝对路径到 SHA256 的映射，覆盖原生预测、评分及可用 HTTP 证据。provenance.execution 以 dataset abbr 为键，固定每个原始评测 process.json 的 {path,sha256}；缺执行回执或完整配置身份需要 review_required，非零回执为 fail，不将两者合并。缺少 HTTP 结束或截断证据时仍解析真实分数，但 evaluation_validity=review_required、status=blocked，不升级为正式基线。只有控制台表格或缺少实际配置/样本身份的旧分数不能导入为已对齐。

## 结果与生命周期

NPU正式run在评测前后委托verify-agent公开 `--action inspect-devices`，要求新的三层编号映射及完整资源身份；缺旧映射时阻断并要求重新verify，不重新部署或停止借用服务。记录每次检查的独立回执，前后只比较稳定映射、有效环境和PID/starttime，报告路径、时间和原始stat运行计数不参与服务身份比较。数据preflight及历史analyze不新增设备检查，GPU基线分支保持原有核验。

EvalScope 在固定输出预算下保留原生评分：HTTP `finish_reason=length` 仅在 `completion_tokens` 等于实际请求中的正整数 `max_tokens`（或 `max_completion_tokens`）且原生 `stop_reason=max_tokens` 时作为已确认的输出上限截断；正常 `stop` 不计截断。原始 token 统计、上下文上限、HTTP 次数、样本覆盖、模型/内容/参考答案对应、原生评分和进程/服务身份仍须通过。未知结束原因、未达到请求输出上限的截断、上下文超限和推理错误继续失败。原生评分不重算，不移除截断样本，不默认给零分或补发请求；AISBench 仍要求正常结束。

每个 EvalScope measurement 新增 `output_truncation:{count,sample_ids,samples,evidence_verified}`。`samples` 保存每个截断样本的 `sample_id`、原生 `stop_reason`、HTTP 对应的 `finish_reason` 及原生逐样本 `native_score`；输出预算和实际 token 数保存在已绑定的请求、响应及配置中。`evidence_verified` 仅表示本项样本与 HTTP 对应证据检查通过，不替代 measurement.valid。未通过 HTTP 证据核对时保留原生截断，但 `finish_reason` 为 null，不声称已确认；历史原生导入仍按既有规则要求复核。新测量满足其他校验可为 `completed/evaluation_validity=pass`；目标另行判定。此策略不改变历史执行有效性，不更新原始基线或将历史失败报告提升为成功。

输出位于 `<work-root>/<run-id>/verify-agent-plus/<独立尝试>/`，不可位于完整安装包、模型、数据或来源文件内，不覆盖旧尝试。共同外层为 schema_version、run_id、task_id、inputs、status、conclusions、evidence、uncovered，并保留 success、summary、detailed_report、report_path。兼容字段 accuracy_evaluation 映射为实际 tasks 指标；environment_check 仅表达本次运行环境复核，分析时保持空值对象；performance_benchmarks 为未执行的空对象，logs 为空列表，原始日志保存在尝试目录，不填历史结果。真实零分为0，缺指标为null，不填写参考分数。HTTP 有效性从原始请求、响应及其摘要重新推导；要求完整 token 统计、明确服务上下文上限及请求模型，拒绝 prompt 截断，并逐项核对原始生成与对应工具 predictions（保留各自原生 reasoning 规则）。

| 项目 | 语义 |
| --- | --- |
| status | not_run/running/completed/failed/blocked/skipped，与目标结论分开。 |
| evaluation_validity | 实际进程、样本覆盖、原生评分和 HTTP 结束证据的有效性。 |
| comparability | 比较条件满足为pass；条件不一致或来源不足为review_required；没有基线为not_evaluated。 |
| precision_target | pass/fail/not_evaluated，按已声明目标逐项判定。 |
| success | 本次有效测试及请求中的目标交付是否满足；没有目标时可为true，但不代表精度达标或 GPU/NPU 对齐。 |
| exit | 0 为计划有效或本次交付满足；1 为执行/证据失败、目标fail或必需目标无法判定；2 为输入/输出边界无效；3 为必需交接或导入证据阻塞；必需解释器、环境或配置探测失败为3；已经开始评测的进程失败为1。 |

原始请求、配置、评测环境、任务/进程回执、HTTP 正文、predictions、results 或 reviews/reports、原生逐样本详情与解析报告保留在包外。指标合法但任务执行失败不能通过；分析不能把历史失败洗成有效测量。每项完成立即保存结果；取消或后续项失败仍保留已完成项。共享上下文通过单独 context_validation 记录，历史分析不会因某项失败抹去其他独立有效项，也不会把服务身份失败提升为有效。

本任务只复用模型服务。取消、超时和异常仅回收自有评测进程组及回环记录器，不停止 PID/CID 交接的服务，不调用历史启动、性能或全局清理脚本。完成后重新核对资源、监听者和实际设备进程；服务变更则不能交付本次有效测量。单次报告的原子写入不构成多文件事务，磁盘不可写时退出失败，不保证能够保存最终报告。
