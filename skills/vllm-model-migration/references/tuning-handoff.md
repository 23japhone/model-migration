# 完整迁移的三 JSON 交付

## 触发和责任

用户意图涉及性能调优时，先完成准备、模型迁移和精度基线。其他 `deployment_precision/run` 完整流程同样必须交付本契约；部分阶段、独立任务及 analyze 不强制生成。
本入口只提供已部署模型和有效基线的交接配置，不执行性能优化，也不以性能或精度分数达标为新门槛。原始精度目标、失败结论、GPU 对齐未验证项及服务生命周期保持原样。

三份配置表达实际部署、评测条件和初始压测负载；原始分数、预测、请求响应、日志和来源摘要继续保存在内部证据目录。本接口只定义文件内容和完成条件，不声明其他 Skill 的实现或调用关系。

## 共用评测环境交接

环境信息通过客户内部 environment 回执与独立核验结果传递，不增加三个 JSON 的字段。性能启动前，Agent 必须调用 [check_evalscope_handoff.py](../scripts/check_evalscope_handoff.py)，只在 ready 后采用本次返回的 benchmark_python、venv、CLI、版本及进程 env；不直接重放安装回执中的旧 PATH。将 Python 填入性能侧已有 benchmark_python，以 argv 列表和子进程 env 传递，保存实际性能解释器及 EvalScope 命令路径并逐项核对。核验失败阻塞性能启动，不改写历史基线，也不安装修复。接口、失败分类及串行约定见[启动前核验契约](evalscope-installation.md#性能启动前只读核验)。

本仓不运行性能任务或自动修改性能插件。核验 ready 仅表示环境交接就绪；缺两端实际运行证据时不能报告共用成功。EvalScope 版本绑定不代表全部依赖锁定。

## 准备候选文件

在实际执行环境、包外目录填写三个候选配置，候选文件必须保持不可变直到导出结束。字段形状见[性能模板](../assets/handoff-benchmark.template.json)、[精度模板](../assets/handoff-accuracy.template.json)、[部署模板](../assets/handoff-deployment.template.json)。精度数据集的 `limit: null` 明确表示所选 split/subset 全量；其余字段仍按各自契约填写，不能因 limit 可空就交付整份空模板。

客户模式由生成器按[customer-baseline-v1](customer-task.md#自动负载-customer-baseline-v1)自动生成负载，不要求客户补填。旧显式导出接口继续接受已有候选配置；不把旧示例数字当默认值，不执行性能测试。缺候选或依据只阻塞导出，不抹去已完成基线。

| 文件 | 核验及映射 |
| --- | --- |
| benchmark_plan.json | service.base_url 为部署回执根地址，无 /v1、凭据或查询串；model 为实际服务名。tokenizer_path 为评测进程可读的模型根，tokenizer_config.json 须有原模型摘要。benchmark 和 runtime_metrics 字段全部显式填写；并发无重复，整数和布尔类型严格区分。 |
| accuracy_plan.json | service 身份同上；evaluation.model_path 使用实际服务名，对应 EvalScope --model。api_key 只允许空字符串或 EMPTY。数据集、顺序、子集、本地路径、limit、eval_batch_size 和超时与本次基线一致。 |
| deployment.json | cann_path 为实际环境可读的 CANN 安装目录/版本文件，来源为原 runtime.env 或绑定同一部署的 API 进程环境快照中的 ASCEND_HOME_PATH / ASCEND_TOOLKIT_HOME；缺少依据或路径冲突则阻塞，不能只凭目录存在推断。env_variables 与设备证据绑定的 effective-runtime.json 中 runtime.env 完全一致；原始 runtime.env 保留不变，只有设备变量允许按真实映射派生。server.model_path 对应 vLLM --model，served_model_name/host/port 及其他字段与原 service.command 一致。 |

两份 Plan 的 output_dir 固定为 `../outputs`，部署 log_file 固定为 `../outputs/server.log`，相对于最终 JSON 所在目录解析，避免复制旧日志/评测目录造成覆盖。模型、tokenizer、CANN 和数据路径使用实际运行主机的规范绝对路径；不把宿主模型路径自动替换成容器路径，也不复制开发机路径。

部署支持直接 `python -m vllm.entrypoints.openai.api_server`、`python -B -m vllm.entrypoints.openai.api_server` 和 `vllm serve MODEL` 的明确 argv；Python 可为 python、python3、python3.x 或对应解释器路径。`-B` 只禁止写入 Python 字节码缓存，不属于 vLLM server 参数；不生成 server.B，原始 argv 仍保存在部署证据中。其他解释器选项和 shell 包装继续阻塞。下划线字段对应连字符选项，模型统一为 model_path。不注入 SGLang 的 tp_size/base_gpu_id/enable_metrics；vLLM 实际参数原样表达。无值开关用 true，需要传递显式 false 的参数用字符串。重复选项、缺少启动 argv、未固定独立 tokenizer 等无法证明无损映射时阻塞，不猜测。借用服务只有已有完整启动证据时可交接；交接不转移服务所有权。

### CANN 的两种证据来源

原 runtime.env 已包含 CANN 路径时，无需另交 CANN 路径快照；仍须有新的设备映射证据。路径由镜像或启动环境继承、未显式写入 runtime.env 时，可在包外新增内部绑定文件，并通过导出请求的可选 `runtime_environment: {path, sha256}` 引用它。绑定文件形状如下（占位路径和摘要必须替换）：

```json
{
  "schema_version": "1.0",
  "deployment": {"path": "/原证据/deployment-handoff.json", "sha256": "原部署交接文件的SHA256"},
  "processes": {"path": "/原证据/actual-processes-and-environments.json", "sha256": "原进程快照文件的SHA256"}
}
```

`deployment` 必须与完整流程固定的部署交接内容一致。`processes` 保留采集时的原始 JSON 数组，每项包含 `identity`、`argv`、`environment`；其他采集字段可保留。导出器从数组中选择唯一匹配原交接 `workers.listener_identities` 的 API 进程，核对整数 pid/pgid、字符串 start_ticks 及完整 argv；不使用 worker 或无关进程的环境。原交接缺少监听进程身份、快照摘要变化、匹配不唯一或命令不同均阻塞。该绑定只证明文件内容和已记录身份一致，不证明当前进程仍存活，也不是签名。

仅从该进程读取两个 CANN 路径变量。显式环境与快照均提供路径时，两边所有已提供的路径必须与 cann_path 一致；不静默覆盖冲突。只要显式提供了快照，就必须完整核验，不能因旧来源可用而忽略坏快照。`env_variables` 使用经过映射验证的有效环境，不补入该路径快照中的 CANN 或其他继承环境。

导出器不执行凭据变量名关键字拦截，也不递归扫描配置字段名来推断凭据。环境变量仍须使用合法名称和值类型；非设备变量与原声明逐项一致，显式设备值不得被静默改写。显式的 evaluation.api_key、服务地址和认证服务适配限制仍按各自契约校验。

设备证据合同见[部署验证契约](../skills/vllm-migration-execute/skills/verify-agent/references/contract.md#设备编号与有效运行环境)。导出只读验证 declared_request/device_mapping/effective_runtime 引用、资源身份、探针库及编号关系，不重新操作设备。旧交接缺失映射时要求新verify证据，不伪造历史记录。三个JSON的结构不变；导出的逻辑编号仅适用于所绑定的已验证载体，换容器后必须重新探测。本能力不修改外部性能优化插件。

客户流程用 verify-agent 的公开 runtime_paths 操作采集可选 path_checks，包含实际 API identity 和 paths 的 is_dir 结果。提供此项时，CANN 与模型 runtime_root 必须在同一 API 进程命名空间为目录；宿主同名目录不能替代。旧请求未提供 path_checks 时保留原宿主路径校验。

绑定文件只进入内部来源记录，不新增三个 JSON 的字段。原部署请求、交接、快照和基线不改写；发布前重新核验所有引用摘要，变化时不发布正式交接。

### 精度生成与评分条件

只导出显式绑定 migration 的评测。部署关联以模型身份及声明部署配置相同为准，不要求同一个容器或同一份部署回执；未绑定的独立评测仍留在原始证据。当前支持带完整冻结 JSON 的 EvalScope Native/openai_api 文本基线；AISBench 不自动改成 EvalScope。读取原任务 detailed_report.tasks[].config，并核对 detailed_report.files 摘要、sample_ids、数据清单与选择摘要；不执行配置、不导入评测 SDK、不重新评分。

不同部署回执关联时，分别读取摘要绑定的 verify/review 和原始部署交接，确认功能验收通过、原声明与交接一致，再比较模型对象（包含文件摘要）、经设备证据验证的有效 runtime（含版本、环境变量、模块摘要及声明 cwd）、服务端点、完整 vLLM 启动参数、Docker 参数和 NPU 配置。参数顺序、`--key=value` 与分开传值、JSON 对象键顺序和空白、Python `-B` 不改变参数含义。Docker 名称、容器/进程 ID、启动时间、日志路径以及验收等待/保留策略不参与配置身份；镜像 ID、设备、挂载和资源设置仍参与。只有名称相同而权重、环境或启动参数不同的服务不等价；缺少原始启动或模型身份证据时不猜测。

此处“同配置部署”用于允许基线跨服务重建交接，不改写实际实例身份。原始基线中的部署引用不替换成当前迁移引用，全部来源继续保留并在发布前重读核验。其他完整流程、基线有效性、样本、评分和模板校验不变；运行期服务身份及生命周期管理仍按各次真实实例处理。

- 每个 benchmark 对应一个数据集对象；本地路径放在 dataset，子集放在 subset_list，其他原 adapter 参数放在 dataset_args。
- limit 保留固定字段：正整数为用户输入的每个 subset 上限 N，null 表示全量；冻结配置省略 limit 时也导出 null。核对原数据清单中的文件行数、选择记录和 sample_ids：全量必须覆盖文件全部记录，限量必须覆盖前 min(N, 行数) 条。不能用实际上限代替实际样本数，不能用全量标记掩盖部分评测。
- 只有同一用户 limit（包括全量 null）、数据路径及其他条件一致时合并多个 subset；各科实际题数不同不再阻止合并。不同 limit 的历史记录不自动取最大值或改成 null，其他切片也不能在导出时改变选择；原始基线始终保持不变。
- max_tokens 必须通过 extra_args 的 `--generation-config` 表达完整 generation_config JSON，不增加数据集条目的同义字段。
- 同时通过 `--model-args`、`--judge` 传递对应 JSON；通过 `--seed`、`--repeats`、`--model-task`、`--model-id`、`--chat-template`、`--eval-backend` 保留冻结配置中非 null 的值。extra_args 为选项和值对，不允许重复、任意附加命令或覆盖基础模型/数据/输出字段。
- 相同 benchmark 的多个子集只有其他配置一致时才合并；不同超时、不同生成配置或重复评测不能静默压成一项。固定实际 EvalScope 版本并核对其 CLI 选项；格式检查不能代替真实执行验收。

字段语义：`limit` 为正整数表示 `--limit N`，为 null 表示不传 --limit，不传字符串 None/null。本仓通过 TaskConfig 显式使用 None 表达全量并冻结配置，不依赖 SDK 隐式样本数。此约定不新增或删除模板字段，也不改变 eval_batch_size 等字段的必填规则。

## 导出接口

[请求示例](../assets/handoff-request.example.json)固定 schema_version=1.0，entry_manifest、benchmark_plan、accuracy_plan、deployment 四项均为 `{path, sha256}`；需要进程环境来源时，增加同样引用格式的可选 runtime_environment。path 为包外普通文件的规范绝对路径，sha256 为本次内容摘要。引用不是签名或可信执行证明。

```bash
python3 "/实际资源/skills/vllm-model-migration/scripts/export_handoff.py" \
  --request "/包外/handoff-request.json" --work-root "/包外/交接根" \
  --run-id handoff-001 --plan
```

`--plan` 只读取顶层请求，核对形状和安装输出边界；ready 不表示候选文件或基线有效，不写目录。去掉 `--plan` 才核验完整来源并发布。

运行 stdout 为单 JSON。成功返回 status=ready、内部 manifest 路径及三个 deliverables 的 path/sha256。退出码：0=计划生成或交接发布；1=取消或发布写入失败；2=顶层请求/输出边界无效；3=来源或候选配置不足、不一致、无法表达。失败输出不得当成正式交付文件。

旧接口默认 --layout legacy，每次运行建立 `work-root/run-id/vllm-model-migration-handoff/UUID/`，保留原三文件目录字面值及独立回执；映射唯一维护在 scripts/tuning_handoff/request.py 中，仅用于布局兼容。客户模式及显式 --layout flat 使用：

```text
work-root/run-id/deliverables/benchmark_plan.json
work-root/run-id/deliverables/accuracy_plan.json
work-root/run-id/deliverables/deployment.json
```

全部校验后整体原子发布；暂存目录不是正式交付。flat 的内部回执位于 customer-workflow/exports/UUID，目录发布后才写 ready；相同内容允许幂等重导出，不同内容拒绝覆盖。写入失败可能仅保留 publication-intent，必须有独立 ready 回执才完成。只使用 ready 指向且摘要一致的三份文件。

最后交付这三个 JSON 文件；不以长报告、空模板或额外格式替代。失败时补齐后只重跑导出。导出本身不刷新服务状态、不执行性能测试、不说明性能提升；客户流程会在导出前另行采集当前服务路径证据。
