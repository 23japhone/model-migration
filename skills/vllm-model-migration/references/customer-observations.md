# Agent 客户流程执行与内部观察

本文件是 Agent 的执行合同。客户输入仍只有 prompt 与 task.json。以下 context 和发现请求由 Agent 在实际运行主机、Skill 资源外生成，不要求客户手写。

## 启动和续接

先完整阅读所用阶段和任务的 SKILL.md、公开契约。客户 `--task --plan` 不读取内部 context、不安装、不下载、不接触设备。运行前确认本次目标主机和操作范围；部署请求已授权的必要准备不重复询问，开发代码授权不等于目标主机运行授权。

context 为 UTF-8 JSON 对象，必需 `schema_version: "1.0"`、`task_source: {path,sha256}`、`intent`、`observations`、`evidence`。intent 为 `deployment/accuracy/performance` 三个布尔值和 `conflicts` 字符串列表；deployment=true 且后两项至少一个 true 才走客户完整流程。evidence 是实际已读资料文件的 `{path,sha256}` 列表，可为文本或 JSON，不能用 URL 代替已读取内容。URL、获取时间与版本应记录在对应资料文件内。

可选顶层项：prompt_fields（Agent 提取的显式字段映射）、decisions、migration_result、evaluation_results、runtime_environment、evalscope_install（可选安装参数对象）。所有引用绑定原始字节；不可把另一 task 的完整结果直接认作本次结果。context 允许按新发现生成新文件；原 task、已解析版本和已执行配方不可变。

首次可只生成意图、空 observations 和证据列表，运行后读 customer-manifest 的每一步诊断。遇到 owner=agent 继续发现；不以“请用户补齐 context”收尾。下一次调用相同 task/run-id，加 `--context 新观察文件 --resume 原customer-manifest.json`。无新的证据或决策时不要循环重复调用。

prompt_fields 必须为对象，只使用 `model.model_name`、`model.path`、`environment.image_name`、`environment.available_npu_devices` 等现有两层字段路径，不新增数组叶子路径；旧名称、deployment 分组与 evaluator_python 均拒绝。原始 JSON 和 prompt 显式值复用同一字段校验，非法值不作为业务冲突。JSON 字段缺失/null/整数 -1 时，先以 prompt 明确参数补齐，再应用静态默认；显式值冲突集中确认。context 统一完成一次合成，环境前置检查与 runner 共享结果，不各自重新补值；合成资产路径在写台账前检查边界，task 来源摘要在创建台账前再次核对。

数据集补值使用完整名称序列的 `accuracy.datasets` 列表：两边已有列表时名称、数量、顺序必须一致，prompt 的每项带 name，其余只写需要补齐或明确的 path/subset_list/limit。未修改项保留为 `{ "name": "gsm8k" }` 等名称条目。字段缺失/null/-1 不清空另一来源的明确要求；双方明确且不同按具体数据集/字段报告冲突。子集列表不合并，不接受索引路径。整组 JSON 未指定时，prompt 决定完整列表；最终声明仍须满足客户完整校验。示例和边界见[客户补值契约](customer-task.md#prompt-补齐数据集字段)。

内部回执 inputs 保留原有归一化客户声明语义，仅用于追溯和续接比较；其静态默认不用于选择实际后端或检查实际资产路径。declared_inputs 冻结合成 prompt 后的声明，effective_inputs 保存实际参数，default_resolutions 保存动态解析值、来源类别、资产/主机身份及证据引用。原 task 不回写。首次解析可在续接中完成；已冻结值或绑定身份变化要求新 run-id，不恢复旧结果。即使一次续接没有 context，冻结记录也保留。模型名和默认容器名未解析时阻塞名称消费者；设备池未解析只阻塞部署。--plan 只作尚未合成 prompt 的 JSON 预览，不读取 context 或运行时证据。

## 公开发现接口

路径相对完整总 Skill 根。除 resolve.py 和原版 install_evalscope.py 外，下列辅助入口使用 --request --work-root --run-id --plan；去掉 plan 才执行。resolve.py 按需要联网读取模型元数据，不下载。install_evalscope.py 的原版参数及准备包装流程见[安装契约](evalscope-installation.md)。

| 所有者/入口 | 请求 | 返回用途 |
| --- | --- | --- |
| 准备 `skills/vllm-migration-prepare/scripts/customer.py` | operation=environment，packages 字符串列表；续接用 versions 映射 | 任务 venv 的 python、实际版本、完整依赖冻结回执；失败后不修改系统环境 |
| 同上 | operation=evalscope_environment，可选 install 对象对应 context.evalscope_install | 每次调用原版安装脚本；返回 python、venv、evalscope_executable、versions.evalscope 与内部 performance_handoff；帮助/预览为 not_run |
| 同上 | operation=image，reference 为客户镜像值（含 -1），platform 为实际 linux/arm64 或 linux/amd64 | 官方稳定发布或明确镜像的冻结身份；本地明确镜像可复用 |
| 模型 `skills/vllm-migration-prepare/skills/vllm-model-prepare/scripts/resolve.py` | `--model` 引用包含客户 model 三字段的对象；也接受 --request 别名 | 本地完整性与身份待复核候选，或明确远程仓库及固定 revision；不下载 |
| 部署 `skills/vllm-migration-execute/skills/verify-agent/scripts/customer.py` | operation=observe，host、port，可选 container_name、capacity=true | 本机检查、空闲端口、NPU 原始表、CPU/内存、同名容器精确身份及 status/running；容量模式另生成结构化设备和来源证据 |
| 同上 | operation=image，image_id，可选 python | 无 NPU 短容器采集安装包版本、源码快照和摘要；只清理本次身份可证的探测容器 |
| 同上 | operation=allocate，spec、devices、requested，可选 policy=idle_first_shared、observation={path,sha256} | 新策略必须绑定本次观察；客户自动采用空闲优先共享策略。未指定策略保留旧独占规则，省略引用仅表示离线计算 |
| 同上 | operation=replace_container，container_name、identity，可选 consent | 无 consent 时仅删除复核仍已退出的精确容器；其他状态须绑定身份的显式授权，不删除镜像/卷 |
| 同上 | operation=runtime_paths，deployment 为 deployment-handoff 引用 | 当前 API 进程 CANN 环境及其命名空间路径证据，供末端导出 |
| 同上 | operation=functional_inputs，model/model_path/files/weight_files/messages；在已有 Transformers 解释器运行 | 只读原生聊天模板、渲染文本及真实 token 长度，不加载模型权重 |
| 精度 `skills/vllm-precision-align/skills/verify-agent-plus/scripts/catalog.py` | model 为服务名、model_path 为本地权重根、dataset 为客户单个数据集对象 | 在固定 EvalScope 解释器运行原生 adapter/loader，读取子集及 token 长度；不推理、不评分 |
| 总入口 `scripts/install_evalscope.py` | 原版 --python/--venv/--version/--upgrade/--dry-run/--help | 原样安装工具；客户流程由准备包装器调用并保存日志、核对结果，不再使用旧候选探测入口 |

EvalScope 客户流程每次使用 evalscope_environment，严格遵循[安装契约](evalscope-installation.md)。首次默认共享 /data/evalscope-venv，可通过内部 context 覆盖；续接省略对象、空对象或部分字段继承上次基础 Python/venv，成功后 version 固定实际版本。upgrade/help/dry_run 仅本次有效。安装选择合成一次，先验证续接身份、继承选择，再检查路径；所有参数通过 argv 传递。内部 evalscope_install_selection 保存原选择与请求来源，evalscope_environment 保存实际绑定，无 context 的中间续接也保留。

显式换环境在安装前要求新任务且不使原绑定失效。已有绑定先做 30 秒只读探测，确认变化或消失才使绑定失效；未知失败只阻塞环境。通过后仍调用安装脚本，超时 1800 秒，保留 stdout/stderr/argv/退出码，末尾 JSON 核验通过才就绪。独立资产不因环境阻塞而停止。help/dry_run 使用独立 environment_preview 回执，绝不覆盖成功环境或继续业务。旧原版安装回执可凭一致请求/结果/绑定恢复选择；证据不足或更早 selection 回执要求新 run-id。

environment 操作只供显式 AISBench 和资产 SDK 使用，不能安装 EvalScope。资产 resolver 首先使用 observations.asset_python 或控制 Python；仅明确缺少 modelscope/huggingface-hub/modelscope-hub 时准备对应单个 SDK 并重试一次。若仍缺其他依赖，由 Agent 检查已有资产解释器或按明确版本补齐独立 SDK 环境，不循环升级。模型解析结果返回实际 asset_python 供模型准备使用。

## 补齐 observations

以下资料可以分多次发现。缺少其中某项会留下具体 blocked 诊断，独立数据准备与部署继续；Agent 负责完成有依据的内部观察。

### 模型、镜像和官方支持资料

- `model_resolution` 为公开模型 resolver 的 result；通常由 runner 自动生成。本地完整性不证明与指定仓库身份一致；存在仓库 ID 时按下节复核。远程权重需要先按 resolver 的固定请求执行模型准备，取得真实 config、文件清单和权重大小后规划部署。
- `image_request` 使用原 image-prepare 公开契约：source/reference/platform、固定 expected_image_id 或 expected_digest、hardware 和官方 recipe。image helper 的 release_url 等发现元数据留在 evidence，不原样混入请求。明确镜像请求需保留客户原 reference。
- `target` 与 `version_review` 使用准备阶段原契约；版本必须匹配实际镜像包，硬件适配结论来自官方版本资料。image helper 不替 Agent 宣告兼容。
- 旧 evaluator_candidates 已移除；安装选项放在顶层可选 evalscope_install，不增加客户 task.json 字段。
- `evaluator` 由本次安装回执填入实际 python/version；不沿用旧观察。`asset_python` 可提供独立资产 SDK 解释器，未提供则先用控制 Python；最终采用 resolver 返回的解释器。本地权重不等待评测环境，资产准备保持独立。

镜像源码探测通过 `find_spec()` 定位顶层普通 Python 包，不导入目标包本身。普通安装要求唯一的安装清单 `包名/__init__.py` 与实际模块路径一致；清单没有源码记录时，仅接受 `direct_url.json` 明确声明 `dir_info.editable=true` 的本地目录，并核对实际模块处于该来源目录内。该规则支持项目根和 `src/` 等布局，不绑定目录名、版本或镜像标签。多个候选、模块遮蔽、来源缺失/损坏/越界均阻塞并给出具体诊断；ZIP、namespace package 和缺少可核验来源的特殊布局不在支持范围。源码快照、摘要、只读探测容器及精确身份清理合同保持不变。

### 模型身份复核

当本地完整模型与显式仓库 ID 同时存在时，Agent 读取 resolver 的 `asset.path`、`asset.identity` 和真实文件清单，再核对下载记录、官方仓库及固定 revision 的资料或文件摘要、缓存来源等证据。目录名称、API model_name、config 中的 `_name_or_path`、相同 architecture 均不能单独证明身份一致。已有可信来源材料足够时直接复用，不强制联网或重新下载模型；证据不足才继续查证，不能把网络失败或缺来源记录判为 match。

Agent 在包外生成复核 JSON，并将其 `{path,sha256}` 写入 `observations.model_identity_review`。这属于 Agent 内部工作文件，不增加客户输入字段：

| 字段 | 约束 |
| --- | --- |
| repository | 当前 model.model_repository_id 的精确值 |
| path | resolver 的实际本地 asset.path |
| identity | resolver 的 asset.identity，含 format=vllm-model-file-list-v1 和 sha256；不得自行构造摘要 |
| conclusion | match / conflict / unknown |
| reason | 非空中文复核说明，明确证据如何支持结论及仍存在的缺口 |
| evidence | 非空的实际已读来源文件引用列表，每项为 path/sha256；可引用原下载记录、官方资料快照或来源清单，不只填写 URL |

脚本验证复核与本次仓库、路径、内容摘要一致，重新读取证据并核对字节摘要；来源资料的语义判断归 Agent，不能把摘要相等当成发布源认证。没有证据时不生成 match。`unknown` 继续由 Agent 查证，必要身份信息无法可靠确定时集中询问；`conflict` 返回 owner=user 的具体决策，保留原目录，不替用户选择另一模型或下载覆盖。

通过后，本地准备请求仍为 source=local，并带 expected_identity；准备入口再核验实际文件。客户台账的 model_identity 保存复核与证据引用。缺复核或检查失败时不调用模型准备、迁移、精度或导出，独立数据准备继续。续接可从本任务上次 model_identity.value.review 取原引用填回 observations，重新检查证据，不改写原复核或历史结果。模型输入改变须新任务；文件内容变化也不能沿用旧身份复核。

只给模型名称不足以确认权重存在；仓库 ID/path 未指定时仍按客户契约进行有界缓存发现。仓库 ID 未指定的完整本地模型仅记录本地内容身份，不声明上游一致。没有完整本地模型时继续按原远程准备合同核验仓库、固定 revision 和逐文件身份。默认 model_name 的来源顺序及架构别名边界见[客户契约](customer-task.md#字段)，读取配置名称不能代替本节身份复核。

### 原生数据集目录

`datasets` 是数据集名到目录对象的映射，每项包含：

| 内部字段 | 来源与约束 |
| --- | --- |
| source | EvalScope 必需，准备前 catalog.json 的 path/sha256；核对客户选择、模型、版本及实际子集顺序。可用 metadata_only=true 只发现科目；准备前 token 长度不进入正式预算 |
| preparation | 原 dataset-prepare 请求，path 必须为客户路径。优先预置原始配方；否则用官方 HTTP 来源或已准备的 local 数据。缺路径时可用 default_local 传官方默认数据位置和资料引用，不能用候选位置覆盖 path。完整原文件清单、格式、split 与摘要来自实际数据 |
| local_path | 兼容旧观察，可省略；正式配置统一使用准备回执 asset.path，不再将该观察字段作为路径来源 |
| dataset_args | 固定版本原生 adapter 需要的其他参数；不替换 loader 或评分器 |
| subsets | 原生实际子集对象列表，每项 name、split、source_file（数据根相对原文件）、metrics（原精度契约的指标 selector/单位/方向）、max_prompt_tokens（旧观察可保留；正式值由准备后的公开 catalog 自动计算） |
| aisbench_config | 仅 AISBench 使用，固定原生 Python 配置的文件引用 |

每个 AISBench 子集还需其原契约 source_columns。AISBench 配置也按客户统一精度方案生成，不能因不导出三文件而省略原生执行与样本核验。

catalog 负责原生 metadata/subset/count/token 长度，不猜 source_file 或 metric；Agent 阅读 adapter 与实际文件完成映射。缺本地路径时，包括 metadata_only 模式，返回 agent_resolve_official_dataset_source，并在 source_resolution 指明 check_default_local_first=true 和原请求 download_path。Agent 不能在该提示处停止：先查官方来源；如官方提供默认本地位置，在 preparation.default_local 填 `{path, evidence:{path,sha256}}`，并把官方资料纳入 context.evidence。prepare 先核验并尝试读取该位置，有效则复用；明确不存在才下载到客户 task.path。没有官方默认位置则省略 default_local，按已核实来源下载到客户路径。已有坏数据、权限错误或资料摘要不符均保留失败，不静默覆盖、换来源或重试。成功后用准备回执再次 catalog，评测路径与预算绑定实际结果；客户输入不变。现有支持配方不覆盖某数据集时，先查官方来源和原生配置；真实歧义再询问。

原生 loader 返回容器须公开提供 `keys()` 和 `[子集名]` 接口；普通字典与 EvalScope `DatasetDict` 均可使用，不要求直接迭代容器或访问内部属性。每次只接受唯一的非空所选子集；缺少接口时诊断包含实际类型和缺失能力。原生样本顺序、最多前 N 条/全量语义、tokenizer 长度计算及评分方式不因容器兼容而改变。

token 长度使用模型聊天模板的实际 token ID 数量，保留生成提示和适用时关闭 thinking 的参数。调用显式指定 `return_dict=false`、`return_tensors=null`、`padding=false`、`truncation=false`；只接受单条样本的非空整数 ID 列表，其他返回形态给出实际类型并阻塞，不能把字典字段数、文本字符数或批次数当成长度。各子集取所选样本的最大值，客户编译器再加 8192 输出预算；修复前产生的错误长度须重新盘点并更新观察引用，不改写历史 catalog。

catalog 在原生加载前后核对整个实际数据目录的文件集合及摘要，发现输出不写入数据目录。新增可选 dataset_manifest={path,sha256} 绑定准备回执；dataset.path 仍保留客户来源路径，实际 local_path 从回执取得。dataset_args 可传原生额外参数，显式 local_path 必须与回执边界一致；metadata_only=true 仅返回原生科目及 split，不加载数据/tokenizer。

客户 C-Eval/GSM8K 的准备请求自动选择 layout=evalscope，原目录保持不变。每个子集准备后，总入口使用该回执再次调用公开 catalog，核对真实原生样本与准备选择，取得正式 max_prompt_tokens；仅此结果进入容量预算和配置生成。其他配方保持原布局，也必须绑定准备回执。某项准备或正式 catalog 失败只阻塞其消费者，不能使用旧观察长度填补。raw→evalscope 的自动布局升级生成新准备回执、配置和引用，保留历史；其他已冻结请求变化仍须按原契约处理。

generic 请求可用 supporting_files 纳入 README、dataset_info 等非样本文件；这些文件只记录大小和摘要，rows=0，不进入选择。selection.file 只选择一个原始文件，limit 仍按该文件最多前 N 行。全量不写 limit。不得仅遗漏目录中文件来绕过原生 local_path 的完整性核验。

### 固定部署配方

`deployment` 是 Agent 根据实际版本/架构/机器形成的内部配方：

| 内部字段 | 依据 |
| --- | --- |
| dtype、model_max_context、runtime_model_path | 模型 config、实际软件支持与挂载布局 |
| sources、reviews | 预检源码快照及官方审阅。Git 来源用完整 commit；安装包用 kind=installed_package、distribution/version/image_id/expected_identity，不伪造 commit |
| runtime | 原 verify 契约的 python、versions、files、env（可选 cwd）；源码摘要须映射到容器实际绝对文件位置。客户编译器管理两个设备变量：ASCEND_VISIBLE_DEVICES取选中物理卡，ASCEND_RT_VISIBLE_DEVICES留待最终载体实测后生成；配方中的旧RT值不作为编号依据 |
| mounts、control_devices、container_resources | 当前主机驱动/CANN/NPU 设备节点、实际挂载及 CPU/memory/shm_size；不复制历史设备或内存数字 |
| registration_check | 原 verify 的 resources/request 绝对路径，须与实际运行载体可读资源一致 |
| weight_markers | 当前版本真实权重加载日志标记，不能使用 dummy load |
| max_num_seqs、server_options | 明确版本的启动参数配方；客户字段、TP、上下文、显存比例由编译器控制，不能用扩展覆盖 |
| thinking_supported、runtime_metrics_available | 当前模型模板和运行指标能力的实际布尔观察 |
| capacity_spec | weight_bytes、kv_bytes_per_token、runtime_bytes_per_device、allowed_tp、memory_fraction；复制 KV 可加 kv_replicated_bytes_per_token。context_tokens 由精度真实长度自动填入 |
| devices | 兼容历史 context 的设备清单；默认设备池首次解析仍核对 ID 覆盖。其 free_bytes/occupied 不再用于客户新部署分配，也不纳入 deployment 冻结比较 |

capacity_spec 的 KV 布局必须匹配模型（如混合注意力、复制 KV、量化缓存的差异）及该版本分片规则；未知不能套一般 MHA 常数。allowed_tp 不按可用卡数随意填写。模型大小从真实权重清单计算，运行开销依据版本和主机证据留出余量。客户 environment.available_npu_devices 是设备使用范围。本次容量观察须覆盖范围内每个 ID；编译器只把本次观察中的范围内设备交给公开 allocate，以 requested=-1、policy=idle_first_shared 按下述策略选卡。范围外设备不能被使用，范围内不足则阻塞。服务 host=127.0.0.1，端口从 8000 起探测，实际值由内部请求冻结。

### 空闲优先与共享部署

客户输入不增加字段。分配先检查全部空闲组合，按合法 TP 从小到大、卡号排序；均不满足时检查含占用卡的组合，按占用卡数量、TP、卡号排序。全占用时仍可选取四卡等合法组合。独立 allocate 未指定 policy 保留原独占行为；显式 requested 列表仍是精确选择，不能自动替换或扩大设备池。

每卡总量为 T、观察到的剩余量为 F。全空闲组合预留 R=0；共享组合每张卡（包括其中空闲卡）均预留 R=ceil(F×5%)。实际比例 u=min(配方 memory_fraction 上限, 所有选中卡的 (F−R)/T)，先按10⁻⁹向下取整，必要时继续向下修正浮点边界，保护乘法不越界。该比例是框架按总显存分配的比例，不能直接把剩余量占比再乘一次配方比例。

逐卡预算 floor(T×u) 必须容纳 ceil(weight_bytes/TP)、runtime_bytes_per_device，以及 context_tokens×max(ceil(kv_bytes_per_token/TP), kv_replicated_bytes_per_token)。5%预留与运行开销分别记录；不能把总剩余显存相加代替分片检查。回执 result 包含 policy、sharing_mode、devices、tensor_parallel_size、memory_fraction、kv_token_capacity、context_tokens、selection_reason，以及逐卡 budgets（原观察、reserve_bytes、budget_bytes、required_bytes、shortfall_bytes）。inputs 绑定本次 observation 和完整 spec；实际 argv、验收声明及交付 server.gpu_memory_utilization 使用同一比例。

显存和 KV 的直接容量覆盖（含 kv_cache_memory_bytes、num_gpu_blocks_override、device_memory_utilization、CPU/KV offload、swap_space）不能绕过核算，编译及启动合同均拒绝。其他启动参数仍须与已核验模型、版本和容量配方一致。

内部 verify 的 npu 可增加 allocation={path,sha256}，指向上述 completed allocate 回执；共享授权来自校验后的回执、观察和预算，不接受独立布尔开关。创建载体前、实际模型启动前分别重新采集容量，核对主机启动身份、设备及固定比例。最新容量不足返回 capacity_insufficient；原全空闲方案出现其他进程返回 resources_changed，需重新规划，不沿用独占回执共享或临时修改 argv。

共享运行允许同卡外部计算进程；每张选中卡仍须有本服务 worker，服务不得越界用卡，载体/PID/映射/解释器身份仍须一致。外部进程单列 external_npu_processes，不进入服务身份签名或清理对象；正常退出或身份变化不单独导致共享验收失败。inspect-devices、精度前后复核及导出使用同一分配证据；运行后不再按启动前空闲显存门槛检查已加载模型的服务。历史分配回执不受新分配60秒时效限制，摘要、原始观察及主机身份仍核对。

有合法 TP 但所有组合不足时，capacity_insufficient 保留候选逐卡预算、预留和 shortfall_bytes；客户流程生成 owner=user 决策，请求更换其他卡或释放显存。TP 不可用返回 tp_unavailable；无效观察、资源变化、驱动/容器不支持共享保留各自诊断，不改写为显存不足。部署失败仅阻塞依赖它的精度与交付，独立资产和数据准备继续。修改 available_npu_devices 依既有输入冻结契约创建新任务；不自动扩大池、停止他人任务或循环重试。原始请求、旧回执、历史结论及三文件结构保持不变。

### 新部署的容量观察

首次部署和失败后的新部署尝试均在原生功能输入观察之后，调用本次公开 `observe` 且指定 `capacity=true`，校验默认设备池的主机身份，再进行分配。不会复用上次 allocation/host_observation 或 context 中的空闲状态；已完成部署及待语义复核的服务仍沿用原身份续接，不重新分配、创建或清理。

容量解析仅支持已知的 910B 整卡 `npu-smi info` HBM-Usage(MB) 布局（25.5.1 格式回归），读取 HBM 已用/总量并乘以1048576转换为字节；不把 Memory-Usage 或 AICore 利用率当作剩余显存/空闲依据。每张卡必须有完整容量行及进程行或明确的 No running processes 声明。健康非 OK、未知型号/单位/布局、缺行、重复卡号、非法 Chip、越界进程或矛盾状态均阻塞；不能以旧 context 兜底。普通未指定 capacity 的 observe 仍保留原始文本供 Agent 识别。

容量结果包含 `devices`、`host_identity={hostname,boot_id}`、`observed_at`（采集开始时的 Unix 秒）和 `npu_evidence={path,sha256}`，同时保留 npu_raw/npu_source。分配请求的 `observation` 绑定本次 completed observe 回执；分配端重新核对当前主机启动身份、原始日志摘要、结构化设备与原文一致，并要求采集至分配不超过60秒且不来自未来。过期明确阻塞，由后续尝试重新采集，不自动重试。分配请求中的设备必须是该观察的原样子集。

用户允许设备池和静态容量/启动配方保持冻结，设备占用及剩余显存不冻结；旧部署配方含 devices 时，续接比较也只排除该动态字段，其他字段变化仍拒绝。分配时容量不足或观察无效发生在同名容器处理之前，只阻塞部署及其消费者，独立资产与数据准备保留。创建和启动前按分配策略复核最新容量，旧无策略请求仍检查独占；观察不构成设备预约或跨进程锁，不能消除采集到启动之间的并发变化。

### 默认设备池

available_npu_devices 未指定时，总入口通过 verify-agent 的公开 operation=observe 取得本机 NPU 原文。Agent 根据实际布局提取完整设备 ID，不能用连续序号、示例或只列空闲卡代替完整清单。内部 observations.device_inventory 为 `{source:{path,sha256}, complete:true, ids:[实际完整ID]}`；source 引用本任务公开 observe 的 observation.json，要求 status=completed、operation=observe，result 包含非空 npu_raw、docker_id、docker_name。脚本不自行解析版本不确定的 NPU 表格，完整性的语义复核由 Agent 负责。

ids 必须非空、唯一、非负整数；deployment.devices 必须覆盖完全相同的 ID 集合，并记录每张卡的实际容量与 occupied。缺完整清单时返回 default_devices 阻塞和补齐指引；显式设备列表不要求该默认观察对象，也不自动扩大。解析后排序并绑定主机身份、候选池和来源摘要；续接重新核对来源，候选池或绑定身份变化要求新任务。部署前再次将公开 host_observation 的 Docker 主机身份与默认池绑定核对，不一致时不进入容器操作。该池表示允许选择的全部设备，分配器按空闲优先及共享逐卡预算选取可行组合。

需要扩展预检时 precheck_optional 仅允许 implementation/adaptation。需要注册/适配时 migration_extensions 仅允许 register/register_container/adaptation/adaptation_roots/timeout_seconds。需要可核验的派生载体时用 derived_image_id 或原 verify build 契约；保留 prepared/requested/build 三种身份，源码变更必须进入 runtime.files。不能覆盖总入口生成的 model/preparation/verify 来跳过检查。

安装包快照只用于静态预检；需要源码适配时，先按 adapt-agent 公共流程取得完整可核验 Git 基线及工作副本，再修改并形成 check 交接。模型服务的创建、观察、重建、启动和保留只能委托 verify-agent；总入口不另行执行 Docker 生命周期命令。

### 镜像来源转换与提前校验

公开 image 观察的 `sources` 可直接作为 `deployment.sources`：其中 `path` 是宿主机源码快照根，`runtime_path` 是容器内包目录，两者不能互换。客户编译器仅将已识别的 `runtime_path` 从预检来源中分离，生成按包名索引的 `runtime_paths`，不修改原始观察。该字段仅允许出现在 installed_package 来源中，须为无 `..` 的非空容器绝对路径；其他未知字段明确拒绝。Git 来源及不含 runtime_path 的既有安装包配方保持兼容；包版本、镜像ID、源码摘要等语义仍由预检公开入口核验。

新部署或未完成部署的续接在取得准备模型后，先生成预检请求并调用公开 `precheck.py --plan`，通过后才执行功能输入观察、容量分配及同名容器处理。该顺序依赖准备回执里的真实模型配置，不表示校验早于模型/镜像准备；独立准备结果仍保留。已有完成部署和语义复核的续接不因此重复启动服务。

客户台账新增 `precheck_plan`：成功记录 not_run，仅表示请求形状有效；正式静态支持度仍由后续迁移预检判定。失败保存 blocked 与诊断，取消保存 failed；每次尝试的命令及 stdout/stderr 单独保存。`source_projection` 引用包含原始来源对象引用、转换后的 `precheck` 来源与 `runtime_paths` 的证据，输入及其摘要保持可追溯。runtime_paths 仅记录容器源码根映射，不自动生成或替代 `runtime.files`，后者仍须声明实际文件及摘要并在载体核验。

### 同名容器替换

新部署前按 task 指定名称发现容器，观察记录完整 `container_id/image_id/started_at` 及 `status/running`。只有 `status=exited` 且 `running=false` 才进入自动替换：总入口将三个身份字段作为 identity 调用公开 replace_container，不生成或传递 consent，也不额外询问用户。其他状态（含 created/dead/未知或字段缺失）仍走显式授权。

自动删除前 verify-agent 以完整 CID 再次 inspect，逐项核对三个身份字段、精确名称和退出状态；不一致或容器已消失时阻塞，不转而删除当前同名对象。使用 `docker rm <完整CID>`，不带 `--force` 或 `-v`；复核后若容器重新运行，由 Docker 拒绝删除，不重试强删。删除后确认原 CID 消失，成功结果仍为 removed 身份和 container_name，随后总入口继续原有创建/启动流程。创建时如出现新的同名容器，正常报冲突，不自动清理新对象。该复核不是跨 inspect/rm 的原子锁，不承诺消除所有并发状态变化。

已完成或可复用的迁移仍沿用原复用逻辑，不因同名发现触发重复清理。每次替换保存独立请求、命令日志和回执；删除失败阻止依赖它的部署，独立步骤保持原失败隔离约定。task.json、CLI 和成功结果结构保持兼容。

### 人工决策和语义复核

运行中或未证实退出的同名容器确认通过后，context.decisions.container_replacement 为 `{"approved":true,"identity":{"container_id":"完整CID","image_id":"sha256:完整摘要","started_at":"实际启动时间"}}`。先向用户展示精确对象；不可凭旧授权自动批准不同身份。实际删除由 verify-agent 再次 inspect 后执行；该旧显式授权接口仍允许强制删除，授权身份不含观察新增的 status/running。格式错误或身份不符的显式 consent 不降级为自动删除。

语义复核必须读原响应。execute 的 semantic_review_required 回执保留服务，Agent 调用原 verify review 与 execute finalize 后将 `{path,sha256}` 写入 context.migration_result。它必须对应该客户流程已生成的迁移请求，不能换入无关模型的 completed 回执。没有复核时 resume 只保留阻塞，不再次部署。

evaluation_results 可显式补充原精度回执；通常无需填写，runner 自动从上次 entry 收集有效项，输入及部署引用相同才复用，并经精度阶段公开 analyze 核验原生产物。改变某项配置不复用另一输入的结果。不要改旧 JSON 的状态或手造通过回执。

## 交付核对

读取客户回执、各阶段原始回执及独立 export-manifest。完整 EvalScope 流程才可能 completed；三个文件必须同时存在且摘要一致。单个阶段 completed、静态计划、合成回归或 AISBench 执行完成都不能代替完整交付。

将环境回执中的 performance_handoff 交给性能插件：同一 benchmark_python、以 CLI 目录为首的 PATH、实际版本；性能运行前重新核验解析结果，详见[共用环境交接](evalscope-installation.md#性能交接与依赖)。不修改三个交付 JSON。

报告实际服务资源状态、当前/历史观察区别、已验证和未验证范围。失败不自动清理他人资源，不无限重试；历史 analyze 不升级、不推理、不重新评分。文件交付只证明迁移、精度基线和配置输出，不证明性能目标达标。


### 功能输入与生成预算

客户新部署在分配设备和替换容器之前，自动使用已绑定 evaluator Python 调用 verify-agent 的公开 customer.py `operation=functional_inputs`。输入由准备回执生成，固定模型名、模型目录、非权重文件摘要、权重文件名及功能题目消息。观察覆盖目录内全部非权重普通文件，已准备文件先核对身份；原生 tokenizer 只读本地模型，trust_remote_code=false，观察前后核对文件集合和摘要。缺依赖、缺模板或渲染异常明确阻塞，不安装修复、不猜模型格式。

观察记录原生模板摘要、Transformers 版本、渲染文本、真实 token 数与 chat_template_kwargs。模板声明 enable_thinking 时尝试传 false；模板不声明则不传，不推断模型一定不思考。渲染失败保留实际原因。显式使用无 padding/截断的单条整数 ID 列表；模板直接分词须与渲染文本按 add_special_tokens=false 再分词完全一致。

文本用例向 /v1/completions 提交原生渲染字符串并关闭重复特殊 token；Chat/SSE 提交相同消息及已观察模板选项。三项共用客户生成预算8192，保持 temperature=0 和语义 review。SSE 请求 include_usage；每项 expected_prompt_tokens 必须与实际 usage.prompt_tokens 完全相等，发现服务端 tokenizer/模板差异时阻塞验收。finish_reason=length、缺少流结束标记、空回答仍不能通过。

部署上下文取精度与功能输入最大长度加8192，再校验模型与设备容量；独立数据尚未就绪时保留原先 model_max_context 策略，同时验证功能输入可容纳。客户 task.json 与三个交付 JSON 无新增字段。显式覆盖 tokenizer/chat_template/相关渲染配置的启动配方当前需另行绑定有效来源，不能当作模型原生模板自动处理；新观察会明确阻塞这些覆盖项，不静默忽略。
