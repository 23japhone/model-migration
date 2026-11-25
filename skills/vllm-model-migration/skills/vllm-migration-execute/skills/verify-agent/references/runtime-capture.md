# msProbe 运行时采集工具

本工具属于 `verify-agent`，直接消费部署验证交接。调用方可以是自己的 Python 程序；无需 GPU 基线、数据集、EvalScope 或 AISBench。完整请求约束见[公开契约](contract.md#msprobe-采集准备)。它提供采集和反查，不判定模型精度是否达标。

## 输入、输出和执行位置

| 环节 | 必需输入 | 可交给下一环节的输出 |
| --- | --- | --- |
| `verify` 准备并启动 | 原部署请求 + `worker_capture`，已声明可写输出路径 | 验证报告、最终部署交接、采集准备回执和配置摘要 |
| `capture-session begin` | 验证报告 `{path,sha256}`、调用控制进程 `owner_pid` | 不可变 `session={path,sha256}`；持久租约 |
| `capture-session arm` | session 引用、`sample_key`、`native_id`、唯一 `trace_id` | 活动请求目录；尚未发送模型请求 |
| 调用模型 | 一条带 `X-Precision-Trace: <trace_id>` 的真实请求 | 调用方保存的原始 HTTP JSON 请求、JSON 或 SSE 响应字节及摘要 |
| `capture-session seal` | session 引用、trace_id、`http.request/response` 摘要引用 | `receipt={path,sha256}`、实际 Token 数、样本身份 |
| `capture-session end` | session 引用 | 封存清单、释放本次租约；模型服务继续原生命周期 |
| `capture-read` | 采集回执、`request_id=trace_id`，按零基 `output_index` 查输出；全程回执也可按 `forward_id` 或零基 `prompt_position` 查前向，或用 `view=timeline` 列出时间线 | 所选前向的输入位置、调用和文件索引，以及适用的输出预测与缓存快照 |

所有动作通过同一个 `scripts/verify.py`，保留 `--request --work-root --run-id --plan`。`--plan` 只做静态检查，不验证来源材料、进程或设备。真实调用在部署的 Linux 宿主侧执行；Docker 使用已有显式可写 bind mount，配置同时记录宿主路径与容器路径。调用方与运行材料必须在同一可见路径空间；不存在自动 SSH 或跨机文件传输。

会话要求自有且 retained 的单请求服务。同步 Eager 文本 greedy 支持单卡和单机多卡纯张量并行，单卡文本服务也可使用 ACLGraph 适配器。单图、SSE 与固定 seed 采样使用单机多卡同步 Eager 会话。完整总 Skill 资源必须可访问，既有共享设备合同是依赖；单独复制 `capture/` 不足以部署。目标解释器必须已经安装兼容 msProbe。工具核对适配器源码、目标 vLLM/vLLM-Ascend 源码及挂点，不自动安装、升级或改变服务并行/图模式。`prepared` 仅证明准备成功，开始会话还需实际 worker、engine、frontend 就绪记录及当前服务身份。

## 无评测器的会话调用

以下是操作请求的形状。将摘要引用替换为实际文件的字节 SHA256；所有输出均放在包外。begin 中 `owner_pid` 是持续存活的调用控制进程 PID。控制进程直接启动每次 CLI 子进程，不能用一个即将退出的中间命令冒充持有者。

```json
{"operation":"begin","source":{"path":"/work/deploy/verification-manifest.json","sha256":"<验证报告摘要>"},"owner_pid":12345}
```

保存标准输出的 `session` 引用，后续操作的 source 指向它，而不是部署报告。

```json
{"operation":"arm","source":{"path":"/work/capture/capture-session.json","sha256":"<session摘要>"},"sample_key":"my-sample-37","native_id":37,"trace_id":"trace-37"}
```

调用方发送一次请求，带上述 trace 头；保存原始 JSON 请求和响应字节。前端观察实际请求 ID、响应 ID，InputProcessor 再观察引擎真实内部 ID，允许内部追加随机后缀。不能以返回文本或自行拼接的 `cmpl-*` 作为关联依据。

```json
{"operation":"seal","source":{"path":"/work/capture/capture-session.json","sha256":"<session摘要>"},"trace_id":"trace-37","http":{"request":{"path":"/work/http/request.raw","sha256":"<请求字节摘要>"},"response":{"path":"/work/http/response.raw","sha256":"<响应字节摘要>"}}}
```

seal 不重发推理。成功后用相同 session 调用 `operation=end`；多题调用方逐题 arm、发送、seal，最后 end。`status` 只查询租约状态。控制请求成功返回 0，形状无效 2，运行或证据失败 1。活动请求失败时保留证据；end 对实际尚未结束的请求拒绝释放租约，防止混入后续采集。工具不自动清理其他持有者或失活租约。

单卡同步 Eager 的 `capture-one` 会发送两次请求，分别关闭和开启采集。单次实际请求或多卡采集使用 `capture-session`。

## 仅采集单题的 Token 预测

在准备了 `worker_capture.adapter="msprobe_pytorch_eager"` 的自有单卡服务上，worker 就绪记录的 `token_prediction=true` 表示支持这种采集。调用 `capture-session begin`，在每个实际评测请求转发前调用以下 arm。字符串或整数 `sample_key` 将作为跨端的样本 ID。

```json
{"operation":"arm","source":{"path":"/work/session/capture-session.json","sha256":"<会话摘要>"},"sample_key":37,"native_id":37,"trace_id":"test-37","profile":"token_prediction"}
```

发送本题唯一的文本请求时设置 `X-Precision-Trace: test-37`，正文显式包含 `temperature=0` 和 `return_token_ids=true`，使用单输出、非流式响应。保存实际发送和接收的 HTTP 正文字节，按原会话协议 seal。服务前端核对请求原件摘要，采集端将采样位置、实际输入、worker 候选、调度器接受 ID 和最终返回逐步配对。worker 按原始分数和选词前分数各自出现的时机暂存一行。runner 返回实际输出 ID 后写入对应的两份向量，无输出的分块 prefill 只保留前向记录。此次 arm 无需声明缓存配置。结束后使用 `capture-session end` 释放租约。

seal 返回 `token_sample={path,sha256}`。该回执引用的单题 JSON 给出 `evidence_kind=runtime`、`sample_id`、`prompt_token_ids`、`output_token_ids`、`stop_reason`、`raw_logits` 和 `processed_probs`。后两项每步各含一条 `{path,sha256,dtype,size}`，其中 path 是相对于样本文件的小端二进制向量路径，size 是词表大小。原始 logits 按实际 dtype 保存。贪心路径直接以分数选词，采集端将真实 argmax 前的选词分数在 CPU 上用 FP64 Softmax 转为比较所需的概率。搬运样本时连同向量一起保留相对路径。stop_reason 记录服务返回的结束原因。长度截断为 length，明确的停止 Token 或字符串分别编码为 stop_token:<id> 和 stop_string:<text>。服务只给出 stop 且无具体触发项时记 unknown_stop。采集回执保留原始 finish_reason 和 stop_trigger。

## 仅采集对齐计划指定的张量和算子

第三遍取证使用同一题的完整原生生成请求。在单卡同步 Eager、单请求文本 greedy 服务上，worker 就绪记录须含 `tensor_alignment=true`。会话通过 `begin` 建立后，在转发原始评测请求前设置以下 arm。

```json
{"operation":"arm","source":{"path":"/work/session/capture-session.json","sha256":"<会话摘要>"},"sample_key":37,"native_id":37,"trace_id":"tensor-37","profile":"tensor_alignment","tensor_plan":{"path":"/work/plans/sample-37.json","sha256":"<计划摘要>"}}
```

`tensor-plan-v1` 按子集和样本 ID 选择一个从 1 起算的输出位置 `p`，冻结实际 prompt、截至 `p-1` 的 Token、模型身份和两端 `torch/vllm/msprobe` 的安装包版本。arm 将计划原件存入运行时共享挂载。worker 用进程内的软件版本和调度器的内部请求 ID、累计输出、有效输入 ID、位置及 logits 行定位目标前向。首个输出对应最后一个 prefill 块。后续位置在同一请求继续 decode。其他前向保留调度元数据，预测向量只为实际返回目标 Token 的前向落盘。生产链路在 `verify-agent-plus tensor-run` 以原生质量评测协议校验部署和权重文件身份，独立 arm 只绑定调用方给出的模型身份及本次部署回执。

`calls` 逐项声明真实模块路径或 msProbe 注册 API 名、直接所属模块、模块内零基 `occurrence`，以及两端的输入、权重、状态、输出槽位。运行时在框架原生回调内筛选，支持 `input_args`、`input_kwargs`、模块 `parameters`、`output` 及原地操作的 `post_input_args/post_input_kwargs`。有效区可用 `capture_slice={"axis":0,"start":1,"stop":2}` 指定半开区间，先切片再向主机拷贝。数值标量存为 0 维张量。其他张量不会出现在此定向配置的 dump 中。捕获范围覆盖模块和已注册框架 API。融合的内部计算需要存在可选择的实际边界。

seal 返回 `tensor_bundle={path,sha256}`。包内张量、两份预测向量和原生调用索引使用相对路径及 SHA256。每个张量可反查实际调用名、父模块、调用次序、参数路径、shape、dtype 及已采切片；原生索引绑定 trace、目标前向、输入位置、预测行、rank 和实测软件版本。缺少计划槽位时，seal 返回 `status=incomplete`、退出码 1，并封存已取得的张量。校验失败或发现额外张量文件时，不签发可比较的结果包。本模式的主机回归覆盖真实 msProbe CPU 采集器及模拟的会话回执，NPU 结果以目标部署的实际采集包为准。

## 从第二个输出 Token 回查

```json
{"source":{"path":"/work/runtime/request-uuid/capture-receipt.json","sha256":"<采集回执摘要>"},"request_id":"trace-37","output_index":1,"layer":"model.layers.1","api":"Torch.add","occurrence":1}
```

使用 `--action capture-read`。prompt 为 `[11,22,33]`、输出为 `[71,92]` 时，第二个输出为 92，实际前文为 `[11,22,33,71]`，decode 消费 71、绝对位置为 3。查询返回这一前向的 `forward_id`、msProbe step、rank、输入行、原始全词表 logits、应用选词规则后的全词表分数、sampler 候选与引擎最终接受值。两份分数独立保存；最终值按调度器接受结果关联，不能只看 argmax 猜测实际输出。

不传 layer/api 时返回该前向的全部已索引调用；layer 可选择实际模块名，包括 embedding、norm 和 lm_head。同层同 API 多次调用用零基 occurrence 区分。每个调用保留父调用、参数位置、shape、dtype、张量文件和 SHA256；布局可确定时记录 Token 切片，否则引用完整张量。共享权重按完整张量引用。原生 .pt 文件可在具有 PyTorch 的读取环境用 `torch.load(path, map_location="cpu", weights_only=True)` 打开；反查工具自身只校验张量文件字节及元数据，不加载张量 SDK。

原生记录中的 `None`、空列表和空字典按实际参数位置保存在 `values`；赋值或 kernel 启动 API 可以没有张量返回值。这些记录不提供额外的写回后张量。缺少输出字段或输出结构无法解析仍报错，输入侧的标量值不能代替输出证据。

每个已开启前向都有 begin/end，分段 prefill 未输出 Token 的前向也核对文件覆盖。全部层指本次文本路径实际执行的模块；算子范围为 msProbe 注册且实际调用的 API，不包含未执行视觉分支、未注册扩展内部或设备 kernel 内部。覆盖清单只能证明这一范围完整。

离线反查不要求服务在线，但必须保留回执引用的配置、角色就绪记录、HTTP 原件、向量、原生元数据及张量文件原路径与原字节。修改部署准备、配置、采集回执、预测向量或张量后不得沿用旧摘要。提前 EOS 可以封存实际短输出；缺少第二个 Token 时查询失败，不制造预测。

## 从一次 decode 回查层缓存

在自有 V1 同步 Eager 服务中，部署请求的 `worker_capture.decode_cache` 接受正整数 `max_snapshot_bytes` 和可选 `layer_scope`。全层范围为 `all`，也是当前缺省值。`legacy_pair` 保留原 Qwen 两层现场协议和文件布局。未声明 `decode_cache` 时走原 msProbe 链路。实际 worker 使用 mindie-debugger 0.1.4。

模型加载后，`worker.json` 的 `architecture` 给出文本层栈、逐层路径、声明类型、实际类与子模块、状态后端和可读的 `diagram`。其他独立栈列入 `other_stacks`。静态预检的 `declared_stacks` 来自原配置。配置数量冲突、候选栈歧义或共享逻辑层无法唯一归属时报告原因。下列示例使用 Qwen 的层号，换模型无需修改挂点路径。

1. `arm` 指定 `cache_output_index=1`，选择生成第二个输出 Token 的 decode。它消费第一个输出 Token。其余会话步骤与普通采集一致。
2. `capture-read` 提交 `source` 回执引用、`request_id` 和 `output_index=1`。结果含 `architecture`、msProbe 调用和 `decode_cache.layers`。每层列出状态、实际调用和错误，未识别状态会标记不完整。完整性覆盖当前文本解码栈。
3. 加入 `layer_index=63` 或 `layer="完整模块路径"`、`cache_snapshot="key.after"` 读指定普通注意力层完整写后 K；`value.after` 读 V。`key.input/value.input` 为待写入值。半开读槽区间来自 FIA 有效块表和长度，写槽来自算子实参。
4. 使用 GDN 层的层号和 `conv.before/after`、`ssm.before/after` 中的具体名称读两种状态。`cache_snapshot="input"` 或 `"output"` 读逻辑层入口首个浮点激活（优先 hidden_states）或出口首个浮点张量；msProbe 另保存其实际支持的其他张量。重复调用可传零基 `occurrence`，多个状态模块可用完整子模块路径消除歧义。
5. 每份数值文件保留原 dtype、shape、SHA256 和预览。读全量数据时用该快照的 `values`；只查询概览时使用文件引用。工作目录应位于有足够剩余空间的文件系统。按 27B 两个代表层的实测尺寸外推，单次 64 层状态快照约 294 MiB，另需边界激活、msProbe 文件及元数据空间。预算为整次 forward 共用。缺层、超预算或未适配的后端使缓存验收为 incomplete，仍可读取已完成层和独立的 msProbe 证据。

单步缓存观测使用单 rank。旧回执保留 `decode_cache.attention/gdn/snapshots` 布局和原查询方式。全层回执使用 `decode_cache.format=2`，按 `layers[].invocations[].states[]` 保存各次算子实参和快照，边界在 `invocations[].boundary`。调度计划继续按同一 `schedule_id/forward_id` 核对。其他执行模式的查询分别见[多卡全程采集](#单机张量并行联合采集)和[单卡图重放](#单卡-aclgraph-文本请求)。

## 从完整请求回查每次前向

在上述部署配置中使用 `layer_scope=all`（默认）。下例在原有 arm 请求中追加一个字段。部署的 `max_snapshot_bytes` 仍是单次前向所有层共同使用的预算。请求总预算跨前向累计，两者均以原始快照字节计。

```json
{"cache_capture":{"mode":"full_request","max_request_snapshot_bytes":6442450944}}
```

以下为 V1 同步 Eager 文本 greedy 的全程配置，要求冷前缀、单请求、单卡或单机多卡纯张量并行。图服务的全程配置见[单卡 ACLGraph 文本请求](#单卡-aclgraph-文本请求)。全程模式使用 `cache_capture`。单卡 Eager 还可用 `cache_output_index` 选择单次 decode，一次 arm 只选其一。两项都省略即可仅启用 msProbe 作为对照。保存原始 HTTP 请求与响应并 seal 后，使用同一份 `source` 和 `request_id`，在 capture-read 请求中选择一种定位方式。

| 想查的内容 | 在请求中加入的字段 |
| --- | --- |
| 所有真实前向、输入位置、接受输出及两套采集量 | `"view":"timeline"` |
| 一个具体前向的调用和缓存 | `"forward_id":"<时间线中的32位十六进制标识>"` |
| 消费某个 prompt 位置的前向，以及第 0 层卷积状态写后原值 | `"prompt_position":0,"layer_index":0,"cache_snapshot":"conv.after"`（有 GDN 的模型） |
| 产生第二个输出的前向，以及普通注意力的写后 K | `"output_index":1,"layer_index":3,"cache_snapshot":"key.after"`（示例为 Qwen） |

位置与输出序号均从零开始。若 4 个输入分成 2+2 两块并生成 3 个 Token，第一块的位置区间是 [0,2)，其 `output_index` 为空；第二块 [2,4) 产生输出 0；后续两次 decode 分别消费输出 0 和 1，产生输出 1 和 2。最后一个产生的 Token 没有再被送入模型时，时间线在产生它的那一行结束。无输出块仍有独立的 msProbe step、rank、层调用与缓存文件。查询无输出块时只返回该前向的证据。

普通注意力按实际位置顺序保存多槽 `key.input/value.input` 和 `key.after/value.after`。读计划说明数据来自本轮输入 K/V 或物理分页缓存，后者给出有效块和槽区间。分块预填充记录各位置的因果上界。GDN 的 `conv.before/after` 与 `ssm.before/after` 对应物理状态行。走分块递归算子时另存 `ssm.initial`，并记录本次实际采用清零初态或既有状态，以及块终态与缓存写回的核对结果。融合算子内部每个位置的状态无法从块首尾推导。后端把单 Token 末块送进更新快路径时，时间线仍按 prompt 位置显示为 prefill，状态记录保留实际算子。

时间线中的 `capture_totals` 汇总原生 msProbe step 目录的文件字节、缓存原始快照字节、msProbe 活跃窗口秒数和缓存观察秒数。msProbe 活跃窗口包含模型计算和缓存观察，两段时间会重叠。原生 msProbe 文件、索引、HTTP 和其他元数据另占空间。超预算或缺少已适配挂点时，查看 `cache_status=incomplete` 和前向的 `errors`。`missing_layers` 列出没有记录调用的层；部分采集的调用以 `missing_boundary` 和 `states[].missing_snapshots` 列出缺少的数值。已保存的部分和独立 msProbe 证据仍可回查。定点查询复验所选前向的原始文件，某一前向的损坏不会阻断其他完好前向的数值查询。


### 单卡同步 Eager 的 MLA 缓存

全层模式按实际加载的缓存实现选择普通注意力、GDN 或 MLA 的观察点。每个状态有调度地址 `address`、持有缓存的已加载模块 `module`、后端类 `backend`。MLA 的 `msprobe_module` 指向实际调用融合实现的外层模块。未识别的后端或未通过源码检查的层在回执中保留原因及其可读的边界证据。

Ascend MLA 目前支持未量化 BF16、PA 布局、128 Token 物理块、单卡 V1 Eager。沿用 `cache_capture.mode=full_request` 和现有请求预算，按 `output_index`、`forward_id` 或 `prompt_position` 选择本次前向，再指定 `layer_index` 与以下快照名称。

| 快照 | 取值位置 |
|---|---|
| `mla.projection.input` | 融合写入算子的原始联合输入，保留归一化与位置旋转前的值 |
| `mla.latent.after`、`mla.rope.after` | 按实际槽号从物理缓存取回的压缩 KV 和位置分量，每行对应本前向的一个有效输入位置 |
| `mla.latent.output`、`mla.rope.output` | prefill 融合写入算子显式返回的两种分量，可逐字节与同位置写后值核对 |

例如 DeepSeek 第二个输出可用 `output_index=1,layer_index=0,cache_snapshot=mla.latent.after`。同一层调用下的 `event` 给出调度预期写槽、元数据槽号及融合算子实收槽号。`backend_path=prefill` 时，`current` 记录本块 FIA 的有效长度和因果掩码，`history` 分次记录已有缓存的起点、长度、物理块和对应 FIA 调用。冷启动时历史列表为空。`backend_path=decode` 时，`decode` 记录分页注意力实收的块表、块长和有效 KV 长度。完整层回执的 `read_ranges` 给出按实参还原的逻辑半开区间。读取物理缓存时同时列出每页有效槽区间，读取本块输入时列出每行的因果截止位置。原始数值文件来自写入边界。指定层和 MLA 快照时，`native_recheck` 报告本次复核的 msProbe 模块、状态及缺失调用。某一分量或原生文件损坏时，仍可查询本层完整的缓存分量。

## 单机张量并行联合采集

在部署请求的 `npu.devices` 中填写所选物理 NPU 列表，将 `--tensor-parallel-size` 设为列表长度。使用 Model Runner V1、同步 Eager、单机 `mp` executor 和一个请求，其他并行维度为 1。启动时核对进程实际加入的 HCCL 组、可见设备与物理卡。四卡和八卡已通过离线契约验证，通信、真实模型分片和 NPU 原值仍待对应规模的实机验证。

例如 `npu.devices=[7,3,0,4]` 配 `--tensor-parallel-size 4`。组内 rank 的范围是 0 至 3，回执中的 `physical_id` 标识实际物理卡。用 `capture-session` 登记真实请求，按需在 `arm` 加上 `cache_capture.mode=full_request`。每个输出只关联一次引擎接受结果。

### 单机 TP=2 联合采集

下表以已有实机记录的双卡配置示例。相同查询也适用于其他卡数，指定 `rank` 时须选该次回执中的组内编号。

时间线的一行表示一次调度且记录每个 worker 实际执行的证据。同一前向的所有 rank 共享 `forward_id`，各自的 `step`、`rank`、层调用、缓存地址和张量文件独立保存。通过 `capture-read` 给出 `source` 和 `request_id` 后，可使用下列字段。

| 查询目的 | 选择字段 |
|---|---|
| 两卡在每次前向的覆盖情况 | `"view":"timeline"` |
| 产生第二个输出时的两卡索引 | `"output_index":1` |
| 卡上第 3 层写后 K 的完整原值 | `"output_index":1,"rank":1,"layer_index":3,"cache_snapshot":"key.after"` |
| 第一块 prompt 的 GDN 卷积更新前值 | `"prompt_position":0,"rank":0,"layer_index":0,"cache_snapshot":"conv.before"` |

输入分块可以没有输出。选定 rank 的读取会检查原生目录、文件摘要、层结构与前向身份，返回该卡本地形状和数据。可证明的 head 分片记录在 `state_shards`。缺少成员、错位或损坏按 rank 标记 `incomplete`，可继续读取完好分片。单前向和请求总快照预算分别在所有参与 rank 间均分；若有余数，从 rank 0 起依次各多分一个字节。msProbe 用量另外统计。`capture_totals.by_rank` 记录每卡用量及观测秒数，`request_wall_seconds` 记录一次请求的实际经过时间。

## 当前采集边界

| 方面 | 当前行为与未覆盖范围 |
| --- | --- |
| 请求与输出范围 | 会话可串行处理多条请求，每次只允许一条在途请求；为每个实际输出 Token 建立索引。sample_key/native_id 由调用方提供，工具不生成评测题目清单 |
| 查询与采集选择 | layer/api/occurrence 只筛选已落盘结果；msProbe 采集范围沿用原配置；可选缓存观察支持一个 decode 或冷前缀完整请求；全程模式使用全层范围 |
| 预测与张量 | 开启数值采集时，greedy 请求保留产生输出所用的 logits 和选词分数，多卡固定 seed 采样可在输出 rank 查到抽样前概率。只为已识别布局提供 Token 切片，其余保留整张量。prompt 每个位置的全词表和每个中间选词步骤超出采集范围。 |
| 执行路径 | 单卡 ACLGraph 已验收 Llama 整图和 Qwen 分段图的文本请求。单机多卡 V1 同步 Eager 支持单图、SSE 和带 seed 的全词表随机采样。多请求并发、多卡图模式、beam、推测解码、音视频和多图仍待适配。 |
| 数据范围 | 实际模块及注册 API 的前向输入输出；可选缓存观察单独提供已适配层的算子边界、地址计划和状态快照。设备 kernel 内部未观测 |
| 存储与恢复 | 当前配置同步保存真实张量；缓存原始字节受单前向预算和全程模式的请求预算限制，msProbe 仍按原配置保存；压缩去重、自动租约恢复和跨机传输未接入。离线查询依赖回执原路径，不是可自动搬迁的数据包 |

这些是本工具的实现边界，不代表 msProbe 上游所有能力的边界。更换模型或版本须重新核对源码、挂点和覆盖；GPU 基线解析、评测编排、数值比较与自动诊断由其他功能负责。

## 验收边界

独立离线测试覆盖公开 CLI、部署摘要交接、本机 HTTP 请求、会话租约、逐 Token 封存和离线反查，并以显式替身代替 Linux 设备观察及 worker 推理。测试使用两层合成模型、完整合成词表和合成张量字节，不是模型实测。

2026-11-17 已完成 Qwen3.5-27B、BF16、单 rank 同步 Eager 的真实 NPU 采集闭环验收，目标版本为 vLLM 0.20.2+empty、vLLM-Ascend 0.20.2rc1、msProbe 26.0.0；核对了采集开关输出一致性，并重新打开第二个输出 Token 对应前向的全部已索引非空输入输出张量。该结论仅对应固定环境和文本请求，不证明其他模型/版本或执行模式通过，也不表示延迟、吞吐和内存开销已达标。其他目标镜像仍须核对源码、挂点、进程身份和实际张量覆盖；性能开销另行测量。

2026-11-25 的单卡同步 Eager 验收另覆盖 DeepSeek-V2-Lite-Chat 的 27 个 MLA 层，并用 Llama 和 Qwen 复验普通注意力与 GDN。DeepSeek 整段和分块请求使用联调版 worker，最终读取器复核两组原件。最终版的分量预算逻辑另经真实 MLA 算子验证。[MLA 查询字段](#单卡同步-eager-的-mla-缓存)

## 启动隔离与目标源码核验

适配器 bootstrap 在主进程及 spawn 重放时移除自身脚本目录的顶层搜索入口，只保留包根；内部的 `adapters.coverage` 不应被第三方 `import coverage` 误认。不需要调用方设置 `PYTHONSAFEPATH` 或额外挂载注册源码来规避同名依赖。

begin 在第一次身份观察写日志之前创建独立尝试目录。arm、seal 和 status 的身份观察也各自使用新的目录，允许调用方使用尚不存在的 work-root/run-id；失败的观察日志保留，不创建成功租约或覆盖旧尝试。--plan 保持零写入。

Chat 前端另审核了源文件 SHA256 `8c41af967ef813bf6fd2e2f16b8eedbbf6098d78c971639a58b6297d1c217c02`：`create_chat_completion(request, raw_request)` 的非流式路径从 `raw_request` 读取 trace，调用 `engine_client.generate(engine_input, sampling_params, sub_request_id, ...)` 并等待最终响应。适配器继续读取实际 generate 参数和响应 ID，不自行重建 chatcmpl ID。离线回归使用该版本真实挂点协议摘录检验位置参数、关键字参数和最终输出绑定；未登记的源码字节仍拒绝。此记录对应最初的非流式契约。双卡生成扩展见本页下节。目标实机结果仍必须由该次部署、采集回执和张量读取证据证明。

部分 vLLM 模型的编译装饰器在 `do_not_compile=True` 时直接调用 `forward()`，绕过 PyTorch 模块 hook。适配器核验装饰器源码摘要后，仅在采集活动期间将目标实例的这一分支接到 PyTorch 标准模块调用，确保外层模型容器也产生 msProbe 输入输出；关闭采集和其他实例保留原调用。就绪记录的 `direct_eager_modules` 列出这些模块。已有局部或全局用户 hook、未知源码或执行模式变化会阻塞该适配，不删减缺失模块来宣称覆盖完整。

## 双卡图文、流式与固定 seed 采样

下表以双卡为例。单机多卡同步 Eager 的 Model Runner V1 按实际 TP 组封存证据，使用单请求和冷前缀。HTTP 请求带 `return_token_ids=true`，流式请求保存客户端实际收到的 SSE 字节。关闭缓存观测时也保留 msProbe 及引擎接受序列。图像用例在服务启动时启用视觉塔，采用单图和视觉权重并行，关闭跨请求前缀和多模态处理器缓存。

2026-11-24 的双卡 NPU 实测封存了 5 次正式请求。Llama 文本流式 greedy 和固定 seed 采样各做了缓存观测开关对照。Qwen 单张红图仅完成关闭缓存观测的请求。红图开启缓存观测时的 K/V、GDN 原值、蓝图正式对照、图文与流式随机采样的组合，以及四卡、八卡的这些生成场景，仍待实机验证。

| 查询字段（另需 source、request_id） | 返回内容 |
| --- | --- |
| `view=timeline` | 前向、各 rank 的本地证据、图片及 SSE 交付记录 |
| `event_index=2` | 客户端第 3 个 SSE 事件，零至多个输出序号和对应前向。用量事件可没有 Token |
| `output_index=1,rank=0` | 原始 logits、筛选后分数。固定 seed 分支还提供抽样前概率、有效 seed、温度、top-k/p、概率最高项和实际候选 |
| `output_index=1,rank=1,layer_index=3,cache_snapshot=key.after` | 产生第二个输出时，本卡文本注意力的计划和写后值。GDN 层沿用 conv/ssm 的前后快照 |
| `output_index=1,image_index=0,rank=1,stack=vision,layer_index=0,image_snapshot=visual.blocks.0.output` | 第二个输出的 text_forward_id 和实际执行视觉编码的 forward_id，附本卡第一视觉层的原生文件索引和输出值 |
| `prompt_position=P,rank=0,image_index=0,image_snapshot=fusion.result` | 读取覆盖 P 的前向在融合位置写入的嵌入。可用的 P 见图像的 feature、fusion_forward_ids 和对应 rank 行 |

SSE 保留重复 Token、空内容和独立用量事件。完成需要终止原因和 [DONE]。随机采样复用服务的实际抽样，要求显式 seed、正温度和 Ascend 全词表同步分支（关闭 enable_reduce_sample、enable_async_exponential）。封存核对概率与分数、候选概率及引擎接受 ID。

图片索引绑定 HTTP 原始字节及摘要、处理器标识和图像占位位置。视觉快照包括 pixels、grid、embedding、fusion.source、fusion.result，以及视觉块、patch_embed 和 merger 的 input/output。每卡保留实际多轴位置与一维 prompt 绝对位置。启用 full_request 时，图文快照共享本卡的单前向和请求预算，msProbe 单独计量。

## 单卡 ACLGraph 文本请求

`worker_capture.adapter="msprobe_pytorch_aclgraph"` 适用于已核对源码的 Ascend Model Runner V1。服务参数指定 BF16、同步调度、关闭前缀缓存、单请求、单卡，以及编译配置中的 `cudagraph_mode="FULL"` 或 `"PIECEWISE"`。目标解释器的准备回执记录 msProbe 版本与源码摘要。探测失败的部署在采集状态中给出原因。模型实测以对应请求的回执为准。

| 服务配置 | arm 设置 | 回执内容 |
|---|---|---|
| `native_dump=false` | 常规 arm | 调度、有效输入、接受结果、实际图重放 |
| `native_dump=true`（默认） | 常规 arm | 增加各前向的 msProbe 原生 Tensor 文件、层归属和完整词表 |
| `native_dump=true` 并声明 `decode_cache` | 加入 `cache_capture={"mode":"full_request","max_request_snapshot_bytes":...}` | 增加文本层输入输出、普通注意力 K/V 或 GDN 状态，以及每份快照的来源 |

部署时确定采集点。做三组生成对照时，为每种配置准备独立的图实例、编译缓存和采集输出目录。`decode_cache.max_snapshot_bytes` 约束单前向导出的原始快照，`max_device_snapshot_bytes` 约束观察器的设备快照分配，缺省取前者。请求预算跨前向累计。msProbe 原生文件单独计量。

`capture-read` 使用 `view="timeline"` 查看无输出的 prefill 块、产生首个输出的最后一块、以及每次 decode。按 `output_index`、`forward_id` 或 `prompt_position` 选择前向。`replays` 列出该前向的图实例、wrapper、批次描述符和重放序号。`layer_index` 选择文本层，`native_files` 列出可检查的原生 Tensor 文件。Qwen 的第二个输出使用 `output_index=1`，第 3 层可查 `key.input`、`value.input`、`key.after`、`value.after`，第 0 层可查 `conv.before`、`conv.after`、`ssm.before`、`ssm.after`。读取层记录中的 `plan` 可查有效槽位或状态行，`sources` 标明图内重放或图外调用。推理结果见 `inference_status`，单前向重放见 `graph_status`，联合证据缺项见 `cache_errors`。

msProbe 原生文件按实际 step、rank、调用名、调用序号与输入输出标识归档。例如 `Module.model.layers.3.…` 可直接归到第 3 层，独立的 `Aten.…` 调用保留在前向级文件索引中。模块融合后的内部层次只在原生记录提供对应名称时归属到层。`api` 与 `occurrence` 可选择该步的具体原生调用。mindie-debugger 的层边界快照保存有效 Token 上的 hidden states。
