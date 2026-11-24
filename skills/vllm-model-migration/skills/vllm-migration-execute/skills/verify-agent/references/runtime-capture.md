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
| `capture-read` | 采集 receipt、request_id=trace_id、零基 output_index | 前文、预测向量、接受 Token、前向和完整调用文件索引 |

所有动作通过同一个 `scripts/verify.py`，保留 `--request --work-root --run-id --plan`。`--plan` 只做静态检查，不验证来源材料、进程或设备。真实调用在部署的 Linux 宿主侧执行；Docker 使用已有显式可写 bind mount，配置同时记录宿主路径与容器路径。调用方与运行材料必须在同一可见路径空间；不存在自动 SSH 或跨机文件传输。

适用自有、retained、单卡或单机 TP=2、单请求、同步 Eager 的服务。文本 greedy 可用单卡或双卡。图文、SSE 与固定 seed 采样扩展使用双卡会话。完整总 Skill 资源必须可访问，既有共享设备合同是依赖；单独复制 `capture/` 不足以部署。目标解释器必须已经安装兼容 msProbe。工具核对适配器源码、目标 vLLM/vLLM-Ascend 源码及挂点，不自动安装、升级或改变服务并行/图模式。`prepared` 仅证明准备成功，开始会话还需实际 worker、engine、frontend 就绪记录及当前服务身份。

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

`capture-one` 是独立诊断入口，内部发送一次关闭采集的对照请求和一次开启采集的请求；需要“只发送一次实际请求”的调用方使用会话接口。

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

单步缓存观测使用单 rank。旧回执保留 `decode_cache.attention/gdn/snapshots` 布局和原查询方式。全层回执使用 `decode_cache.format=2`，按 `layers[].invocations[].states[]` 保存各次算子实参和快照，边界在 `invocations[].boundary`。调度计划继续按同一 `schedule_id/forward_id` 核对。GPU/NPU 图重放、并行 rank 或未执行模态需要分别适配和验收。

## 从完整请求回查每次前向

在上述部署配置中使用 `layer_scope=all`（默认）。下例在原有 arm 请求中追加一个字段。部署的 `max_snapshot_bytes` 仍是单次前向所有层共同使用的预算。请求总预算跨前向累计，两者均以原始快照字节计。

```json
{"cache_capture":{"mode":"full_request","max_request_snapshot_bytes":6442450944}}
```

此模式要求冷前缀、一个实际请求、单卡或单机 TP=2 的 V1 同步 Eager、文本 greedy。全程与单输出模式分别使用 `cache_capture` 和 `cache_output_index`，一次 arm 只选其一。两项都省略即可仅启用 msProbe 作为对照。保存原始 HTTP 请求与响应并 seal 后，使用同一份 `source` 和 `request_id`，在 capture-read 请求中选择一种定位方式。

| 想查的内容 | 在请求中加入的字段 |
| --- | --- |
| 所有真实前向、输入位置、接受输出及两套采集量 | `"view":"timeline"` |
| 一个具体前向的调用和缓存 | `"forward_id":"<时间线中的32位十六进制标识>"` |
| 消费某个 prompt 位置的前向，以及第 0 层卷积状态写后原值 | `"prompt_position":0,"layer_index":0,"cache_snapshot":"conv.after"`（有 GDN 的模型） |
| 产生第二个输出的前向，以及普通注意力的写后 K | `"output_index":1,"layer_index":3,"cache_snapshot":"key.after"`（示例为 Qwen） |

位置与输出序号均从零开始。若 4 个输入分成 2+2 两块并生成 3 个 Token，第一块的位置区间是 [0,2)，其 `output_index` 为空；第二块 [2,4) 产生输出 0；后续两次 decode 分别消费输出 0 和 1，产生输出 1 和 2。最后一个产生的 Token 没有再被送入模型时，时间线在产生它的那一行结束。无输出块仍有独立的 msProbe step、rank、层调用与缓存文件。查询无输出块时只返回该前向的证据。

普通注意力按实际位置顺序保存多槽 `key.input/value.input` 和 `key.after/value.after`。读计划说明数据来自本轮输入 K/V 或物理分页缓存，后者给出有效块和槽区间。分块预填充记录各位置的因果上界。GDN 的 `conv.before/after` 与 `ssm.before/after` 对应物理状态行。走分块递归算子时另存 `ssm.initial`，并记录本次实际采用清零初态或既有状态，以及块终态与缓存写回的核对结果。融合算子内部每个位置的状态无法从块首尾推导。后端把单 Token 末块送进更新快路径时，时间线仍按 prompt 位置显示为 prefill，状态记录保留实际算子。

时间线中的 `capture_totals` 汇总原生 msProbe step 目录的文件字节、缓存原始快照字节、msProbe 活跃窗口秒数和缓存观察秒数。msProbe 活跃窗口包含模型计算和缓存观察，两段时间会重叠。原生 msProbe 文件、索引、HTTP 和其他元数据另占空间。超预算或缺少已适配挂点时，查看 `cache_status=incomplete` 和前向的 `errors`。`missing_layers` 列出没有记录调用的层；部分采集的调用以 `missing_boundary` 和 `states[].missing_snapshots` 列出缺少的数值。已保存的部分和独立 msProbe 证据仍可回查。定点查询复验所选前向的原始文件，某一前向的损坏不会阻断其他完好前向的数值查询。

## 单机 TP=2 联合采集

在自有服务上声明两张物理 NPU、`--tensor-parallel-size 2` 和本机 `mp` executor。其余并行维度均为 1。使用 `capture-session`，两次请求均启用 msProbe，按需在 `arm` 加入已有的 `cache_capture.mode=full_request`。服务准备和会话需要两名真实 worker 的 TP 组、进程与可见设备回执。服务记录唯一的 executor 输出 rank，最终输出以引擎接受结果为准。

时间线的一行表示一次调度且记录每个 worker 实际执行的证据。两卡共享 `forward_id`，各自的 `step`、`rank`、层调用、缓存地址和张量文件独立保存。通过 `capture-read` 给出 `source` 和 `request_id` 后，可使用下列字段。

| 查询目的 | 选择字段 |
|---|---|
| 两卡在每次前向的覆盖情况 | `"view":"timeline"` |
| 产生第二个输出时的两卡索引 | `"output_index":1` |
| 卡上第 3 层写后 K 的完整原值 | `"output_index":1,"rank":1,"layer_index":3,"cache_snapshot":"key.after"` |
| 第一块 prompt 的 GDN 卷积更新前值 | `"prompt_position":0,"rank":0,"layer_index":0,"cache_snapshot":"conv.before"` |

输入分块可以没有输出。选定 rank 的读取会检查原生目录、文件摘要、层结构与前向身份，返回该卡本地形状和数据。可证明的 head 分片记录在 `state_shards`。缺少成员、错位或损坏按 rank 标记 `incomplete`，可继续读取完好分片。单前向和请求总快照预算分别在两个 rank 间均分，余下的一个字节分给 rank 0。msProbe 用量另外统计。`capture_totals.by_rank` 记录每卡用量及观测秒数，`request_wall_seconds` 记录一次请求的实际经过时间。

## 当前采集边界

| 方面 | 当前行为与未覆盖范围 |
| --- | --- |
| 请求与输出范围 | 会话可串行处理多条请求，每次只允许一条在途请求；为每个实际输出 Token 建立索引。sample_key/native_id 由调用方提供，工具不生成评测题目清单 |
| 查询与采集选择 | layer/api/occurrence 只筛选已落盘结果；msProbe 采集范围沿用原配置；可选缓存观察支持一个 decode 或冷前缀完整请求；全程模式使用全层范围 |
| 预测与张量 | 保存输出预测的原始 logits 和最终 greedy 选词分数；不逐项记录中间选词处理，也不采集 prompt 每个位置的全词表 logits。仅对已识别布局提供 Token 切片，其余保留整张量 |
| 执行路径 | 双卡 V1 适配单图 Qwen3.5、SSE、带 seed 的全词表随机采样。并发批处理、图模式、beam、推测解码、音视频和多图留待独立适配。 |
| 数据范围 | 实际模块及注册 API 的前向输入输出；可选缓存观察单独提供已适配层的算子边界、地址计划和状态快照。设备 kernel 内部未观测 |
| 存储与恢复 | 当前配置同步保存真实张量；缓存原始字节受单前向预算和全程模式的请求预算限制，msProbe 仍按原配置保存；压缩去重、自动租约恢复和跨机传输未接入。离线查询依赖回执原路径，不是可自动搬迁的数据包 |

这些是本工具的实现边界，不代表 msProbe 上游所有能力的边界。更换模型或版本须重新核对源码、挂点和覆盖；GPU 基线解析、评测编排、数值比较与自动诊断由其他功能负责。

## 验收边界

独立离线测试覆盖公开 CLI、部署摘要交接、本机 HTTP 请求、会话租约、逐 Token 封存和离线反查，并以显式替身代替 Linux 设备观察及 worker 推理。测试使用两层合成模型、完整合成词表和合成张量字节，不是模型实测。

2026-11-17 已完成 Qwen3.5-27B、BF16、单 rank 同步 Eager 的真实 NPU 采集闭环验收，目标版本为 vLLM 0.20.2+empty、vLLM-Ascend 0.20.2rc1、msProbe 26.0.0；核对了采集开关输出一致性，并重新打开第二个输出 Token 对应前向的全部已索引非空输入输出张量。该结论仅对应固定环境和文本请求，不证明其他模型/版本或执行模式通过，也不表示延迟、吞吐和内存开销已达标。其他目标镜像仍须核对源码、挂点、进程身份和实际张量覆盖；性能开销另行测量。


## 启动隔离与目标源码核验

适配器 bootstrap 在主进程及 spawn 重放时移除自身脚本目录的顶层搜索入口，只保留包根；内部的 `adapters.coverage` 不应被第三方 `import coverage` 误认。不需要调用方设置 `PYTHONSAFEPATH` 或额外挂载注册源码来规避同名依赖。

begin 在第一次身份观察写日志之前创建独立尝试目录。arm、seal 和 status 的身份观察也各自使用新的目录，允许调用方使用尚不存在的 work-root/run-id；失败的观察日志保留，不创建成功租约或覆盖旧尝试。--plan 保持零写入。

Chat 前端另审核了源文件 SHA256 `8c41af967ef813bf6fd2e2f16b8eedbbf6098d78c971639a58b6297d1c217c02`：`create_chat_completion(request, raw_request)` 的非流式路径从 `raw_request` 读取 trace，调用 `engine_client.generate(engine_input, sampling_params, sub_request_id, ...)` 并等待最终响应。适配器继续读取实际 generate 参数和响应 ID，不自行重建 chatcmpl ID。离线回归使用该版本真实挂点协议摘录检验位置参数、关键字参数和最终输出绑定；未登记的源码字节仍拒绝。此记录对应最初的非流式契约。双卡生成扩展见本页下节。目标实机结果仍必须由该次部署、采集回执和张量读取证据证明。

部分 vLLM 模型的编译装饰器在 `do_not_compile=True` 时直接调用 `forward()`，绕过 PyTorch 模块 hook。适配器核验装饰器源码摘要后，仅在采集活动期间将目标实例的这一分支接到 PyTorch 标准模块调用，确保外层模型容器也产生 msProbe 输入输出；关闭采集和其他实例保留原调用。就绪记录的 `direct_eager_modules` 列出这些模块。已有局部或全局用户 hook、未知源码或执行模式变化会阻塞该适配，不删减缺失模块来宣称覆盖完整。

## 双卡图文、流式与固定 seed 采样

图文、SSE 和固定 seed 随机采样沿用单请求、冷前缀、TP=2、同步 Eager、Model Runner V1 会话。HTTP 请求带 `return_token_ids=true`，流式请求保存客户端实际收到的 SSE 字节。关闭缓存观测时也保留 msProbe 及引擎接受序列。图像用例在服务启动时启用视觉塔，采用单图和视觉权重并行，关闭跨请求前缀和多模态处理器缓存。

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
