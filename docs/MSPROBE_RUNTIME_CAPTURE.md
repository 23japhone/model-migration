# msProbe 运行时采集交付

## 2026-11-26 单卡同步批次中的请求身份

Llama-3-8B-Instruct 的单卡 V1、同步 Eager、BF16、greedy、冷前缀短测使用 7 和 97 个 prompt Token，并发请求各生成 12 个 Token。两条请求的实际前文、输出和结束原因与关闭身份采集时一致。交付版记录了 12 次共享前向，长请求的 3 个无输出 prefill 块可按 prompt 位置查询。停服后复查同一个前向，短请求在请求行 1 消费位置 0 至 6，得到首个输出 82；长请求在请求行 0 消费位置 96，得到首个输出 8。两条结果各自绑定 worker 输入、采样行和引擎接受记录。

`batch_identity` 只记录元数据，本次实测没有生成数值张量文件。同步批次数值由独立的 `batch_joint` 模式采集。异步调度待适配。[调用与查询](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#查询同一次前向中的多个请求)

## 单卡 Eager 的同步批次联合采集

Llama-3-8B-Instruct 使用两个并发文本请求，分别输入 7 和 35 个 Token，生成 4 和 3 个 Token。身份对照、msProbe 对照和联合采集均出现 5 个物理前向，其中 3 个共享。输出分别为 `[82,220,16,267]` 和 `[71,23,44]`。联合组按两条成员时间线逐前向核对了全部 32 层。

短请求的第二个输出对应一次 decode，消费 Token 82。长请求在同一前向首次 prefill，消费 31 个 Token。公开入口查回两成员首末层的 BF16 激活，形状分别为 `[1,4096]` 和 `[31,4096]`。四份 msProbe 切片的摘要都与各自的 mindie 层输出一致。副本删除一个成员的 `key.after` 后，仍能读回该成员的 `value.after` 及同批另一个成员的 `key.after`。

本组联合采集的 msProbe 原生目录共 341.61 GiB，mindie 成员快照共 35.25 MiB。两者均为该请求组的实际用量。对照组在保存索引、摘要及首末层样本后释放了原生张量目录。

Qwen3.5-27B 的相同对照也通过。短请求生成 `[198,220,471,803]`，长请求生成 `[71,23,44]`。两条时间线各含 4 次执行，每次覆盖 16 层普通注意力和 48 层 GDN。短请求按第二个输出、长请求按首个 prompt 位置查到同一次前向。两请求第 3 层 K/V 分别写入槽 32263 和 38400–38430，第 0 层 GDN 分别沿用状态行 22 和在行 26 从零初始化。48 层的卷积、递归状态逐请求核对，相邻执行共有 576 对前后摘要一致。

Qwen 两成员的首末层激活分别为 `[1,5120]` 和 `[31,5120]`，msProbe 与 mindie 的四份原值摘要一致。两种数值配置下，每个请求每次执行的全词表 logits 摘要一致。验收副本移除短请求的 `ssm.after` 后，公开入口仍读得到其 `conv.after` 和同批长请求的 `ssm.after`。联合组 msProbe 索引原件共 620.76 GiB，成员快照共 3133.63 MiB。

## 单卡 Eager 的跨请求前缀缓存

在 Ascend 910B2 上，使用 Model Runner V1、同步 Eager、BF16、greedy 和本地前缀缓存。在同一个服务内依次发送 A、B，两者共享开头的 Token，最后两个输入不同。冷缓存 B、热缓存只开 msProbe 的 B 和热缓存联合观测的 B 使用相同的输入及生成参数。三组逐一核对实际输入、三个输出和每个输出之前的前文。另用热缓存命名空间只改 B 的首个 Token，检查查找返回零命中。

| 模型 | B 命中且逐层核实的 Token 数 | 已验证的缓存分量 | 三组 B 的输出 Token ID | 六个正式请求的 msProbe 原件 / 缓存快照 |
| --- | ---: | --- | --- | --- |
| Llama-3-8B-Instruct | 128 | 32 层普通注意力的 K、V | 61, 198, 33 | 484.30 GiB / 116.50 MiB |
| DeepSeek-V2-Lite-Chat | 128 | 27 层 MLA 的 latent、RoPE | 61, 7211, 33 | 946.83 GiB / 43.93 MiB |
| Qwen3.5-27B | 1536 | 16 层普通注意力的 K、V，48 层 GDN 的卷积、递归检查点 | 45, 19, 7211 | 2371.23 GiB / 4116.38 MiB |

Llama 和 DeepSeek 的消费请求从位置 128 起做 prefill。公开查询位置 127 时返回 A 写后原值、B 读到的原值及各自摘要，查询位置 128 时返回 B 实际执行的前向。第二、第三个输出各自对应一次 decode。把 Llama 一层的来源文件在隔离挂载环境中隐藏后，查询指出缺失的层和分量，同一层的另一分量及 B 的后缀仍可读取。退出隔离后已复核原件摘要。[按请求和位置查询的契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#跨请求前缀缓存命中)

Qwen3.5-27B 在这个构建中使用 `mamba_cache_mode=align`。共享 128 或 512 个 Token 的实机探针均为零命中，共享 2048 个时命中 1536 个。正式采集使用 1536 个共享 Token 和两个不同的尾部 Token。B 从位置 1536 开始执行，在第 3 层读取物理槽区间 `[7680,9216)` 的历史 K/V，后缀写入槽 19968、19969。第 0 层 GDN 将 A 的物理行 6 复制到 B 的行 14，卷积状态 61,440 字节、递归状态 3,145,728 字节均与 A 在该边界的写后值一致。这些行号和槽号来自本次分配。48 个 GDN 层均按边界检查点比较，16 个普通注意力层均按位置比较 K 和 V。

Llama 的三组对照先使用联调版 worker，随后用交付版重新采集 A、B，两版的输入、逐步前文和输出完全相同。交付版再次通过 32 层、64 个缓存分量的来源核验。三个模型均核对了实机 worker 加载的 45 个适配器文件与验收版本的摘要。提交时移除了 `decode_cache/prefix/values.py` 文件末尾的一行空白，其余 44 个适配器文件与实机版本逐字节一致。表中的用量仅统计各模型的六个正式请求。

## 单卡 Eager 的 MLA 缓存

在 Ascend 910B2 上，DeepSeek-V2-Lite-Chat 的 V1 worker 自动识别到 27 个 `AscendMLAImpl`。该模型的每个槽位保存 512 维压缩 KV 与 64 维位置分量。真实融合算子探针用不连续物理页验证两个分量及相邻槽，decode 写后值与独立小缓存一致。

9 个输入 Token 的请求在同一服务上分别开启、关闭缓存观测，两次都开启 msProbe，均生成 `[19, 11, 2494]`。开启组的三个前向均覆盖 27 层。按第二个输出回查，实际输入为 `19`，输出为 `11`，逻辑位置为 9。本次分配中，分页读取的有效物理槽为 `[256,266)`，写槽为 265。第 0 层 latent 写后文件形状为 `[1,1,512]`，第 26 层 RoPE 写后文件形状为 `[1,1,64]`。三次前向的每一层都用 msProbe 融合算子原件独立核对输入、latent 和 RoPE，243 份数值的原始字节一致。

130 个输入 Token 实际分成 128 和 2 两块，开启与关闭观测均生成 `[11,9156,218]`。第一块无输出，第二块的 27 层均通过 `kv_cache_load` 加载前 128 个位置，再处理本块的两个位置。按第二个输出回查，decode 消费 `11`、生成 `9156`，写物理槽 642，有效读槽分属两页，区间为 `[512,640)` 和 `[640,643)`。四个前向的 324 份融合输入与写后分量均和 msProbe 原件逐字节一致。

同轮在 Llama-3-8B-Instruct 和 Qwen3.5-27B 上分别运行 9 个输入、2 个输出的开关对照。两模型均生成 `[19,11]`。两个前向分别覆盖 Llama 的 32 层普通注意力和 Qwen 的 16 层普通注意力、48 层 GDN。逐文件复核开启组的缓存原值，Llama 共 256 份，Qwen 共 560 份，其中 48 份为 prefill 的递归有效初态。两模型的首末层 msProbe 原生输出均已反序列化。

| 开启观测的请求 | msProbe 原件 GiB | 层边界及状态快照 MiB | 缓存观察耗时秒 |
|---|---:|---:|---:|
| DeepSeek 整段 | 243.85 | 3.24 | 0.64 |
| DeepSeek 分块 | 325.53 | 39.53 | 2.03 |
| Llama 整段 | 123.49 | 7.50 | 0.75 |
| Qwen 整段 | 241.08 | 745.00 | 27.30 |

在隔离挂载环境改写单层 latent 文件的一字节后，公开查询定位到文件摘要变化，并能返回同一层的完整 RoPE 数据。DeepSeek 完整模型对照使用本轮联调版 worker，当前读取器重验两组正式原件。最终版分量预算逻辑另经真实 MLA 算子验证，预算为 128 字节时成功保存 RoPE 原值，并分别指出融合输入、latent 超限。读取参数和字段见[单卡 MLA 采集契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#单卡同步-eager-的-mla-缓存)。

## 单卡 ACLGraph 纯文本生成

在 Ascend 910B2 上使用 Model Runner V1、BF16、同步调度、greedy 和冷前缀。每个用例分别使用原生图、图上开启 msProbe、图上开启 msProbe 与缓存观测三套配置，独立编译和捕图。12 次正式请求的实际输入、调度分块、逐步前文和输出 Token ID 均按用例核对一致。

| 模型 | 实际 prefill | 三组一致的输出 Token ID | 每个前向的实际重放 | 联合采集每个前向的状态覆盖 |
|---|---|---|---|---|
| Llama-3-8B-Instruct | 9 | `[19,11,220]` | 1 次整图 | 32 层注意力 |
| Llama-3-8B-Instruct | 32+1 | `[18,11,220]` | 1 次整图 | 32 层注意力 |
| Qwen3.5-27B | 9 | `[19,11,220]` | 65 次子图 | 16 层注意力、48 层 GDN |
| Qwen3.5-27B | 32+2 | `[11,220,16]` | 65 次子图 | 16 层注意力、48 层 GDN |

整段输入经过三个前向，分块输入经过四个。第一个分块按 prompt 位置可查，最后一个分块产生第一个输出。两次连续 decode 复用同一整图或同一组子图，重放序号递增。两模型均通过首末层 msProbe 原件回读、形状与摘要核对。

Qwen 分块用例中，按 `output_index=1` 查到的 decode 消费 Token `11`，产生 `220`。第 3 层 FIA 使用分页 K/V，有效长度 35，后端页号 108，写入物理槽 13858。K 和 V 的待写值与各自写后值逐字节一致，形状均为 `[1,4,256]`。同一前向的第 0 层在状态行 1 更新卷积状态（BF16，`[3,10240]`）和递归状态（FP32，`[48,128,128]`）。层输入输出来自子图重放，这两类缓存更新来自前向内的图外算子。两块 prefill 之间，48 层 GDN 共 96 份状态的首块终值摘要和下一块初值摘要一致。

Qwen 联合采集的整段请求分别写入约 150.18 GiB 的 msProbe 原件和 1184 MiB 的快照，分块请求分别约为 202.10 GiB 和 1800 MiB。两次 decode 均完成后重读较早的一份快照，摘要一致。在回执副本中移除第 3 层的 `key.after`，公开查询返回 `incomplete`，并保留同一前向的 `value.after`。部署参数、查询选择器和字段见[单卡图采集契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#单卡-aclgraph-文本请求)。

## 双卡图文、流式和固定 seed 生成

2026-11-24 在两张 Ascend 910B2 上使用 Model Runner V1、BF16、同步 Eager 和 msProbe。验收按实际封存的 5 次正式请求收口。用户接受本次交付，实机覆盖见下表。查询方法见[图文和生成事件契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#双卡图文流式与固定-seed-采样)。

| 用例 | 实机结果 |
| --- | --- |
| Llama 文本流式 greedy | 缓存观测开关均生成 `[220,18]`，两个 rank 各覆盖 32 层。公开查询将两个输出事件分别关联到 prefill、decode，独立用量事件没有输出序号。 |
| Llama 固定 seed 随机采样 | 开关均生成 `[22843,315]`。首步 argmax 为 2362，seed 123 实际抽到 22843，过滤后概率约为 0.102811。 |
| Qwen 单张红图 greedy | 关闭缓存观测的正式请求回答 red，输出 `[2438,248046]`。占位区间为 `[4,68)`，两次 prefill 分别融合 60、4 个图像嵌入。两个 rank 均有 27 个视觉块和 64 个文本层的对应执行记录。两卡的视觉首末块输出样本均为 `[256,1,1152]`，产生第二个输出的文本第 3 层样本为 `[1,5120]`。 |

红图开启缓存观测、蓝图正式对照及图文流式随机组合在本轮收口时未执行。蓝图的无采集预检回答 blue。图文场景的第 3 层 K/V 和第 0 层 GDN 写后或前后原值仍待双卡实测。下节记录此前纯文本场景的独立结果。

## 单机双卡张量并行

TP=2 采集沿用 Model Runner V1、同步 Eager、greedy、单请求和冷前缀。调度器广播前向身份，两个 worker 在实际模型前向中分别记录本地 msProbe 张量和缓存快照。引擎为每个输出保留一次接受记录。公开读取方式、预算和失败状态见[双卡查询契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#单机-tp2-联合采集)。

Llama-3-8B-Instruct 的单 Token 短请求已在两张 Ascend 910B2 上读回同一次前向的两份写后 K。每个 rank 有 32 层，第一层的 K 形状均为 `[1,4,128]`，为本地 4 个 KV heads。两个原生 msProbe 目录共约 86.9 GiB。独立快照副本中缺少 rank 1 的 `key.after` 时，公开查询定位到对应层和文件，rank 0 仍可读取。


### Llama 和 Qwen 的双卡完整生成对照

两张 910B2 上的 8 次正式请求均使用 msProbe。每组在同一服务实例中依次开启、关闭全程缓存观测。28 次逻辑前向对应 56 份 worker 记录。开启组的每个前向均封存两卡的本地快照，关闭组继续封存两卡的 msProbe 原件。

| 模型 | 实际 prefill | 开、关一致的输出 Token ID | 每个 rank 覆盖的文本层 | 开启组两卡合计 msProbe / 缓存快照 |
|---|---|---|---|---|
| Llama-3-8B-Instruct | 9 | `[19, 11, 220]` | 32 层 attention | 375.61 GiB / 13.75 MiB |
| Llama-3-8B-Instruct | 32+1 | `[18, 11, 220]` | 32 层 attention | 520.60 GiB / 43.75 MiB |
| Qwen3.5-27B | 9 | `[19, 11, 220]` | 16 层 attention、48 层 GDN | 373.19 GiB / 1053.75 MiB |
| Qwen3.5-27B | 32+2 | `[11, 220, 16]` | 16 层 attention、48 层 GDN | 504.54 GiB / 1557 MiB |

Qwen 为每个 rank 固定 4 GiB KV 池，运行时报告容量为 10,752 个 Token。两组模型均关闭前缀复用，使用相同的 BF16、V1、同步 Eager 和 greedy 配置。对照以请求内的 prompt、逐步前文和全部输出 ID 为准。

Qwen 分块组的第二个输出为 220，decode 消费 Token 11。两卡的第 3 层都从 paged KV 读取，后端的有效槽区间为 `[19968, 20003)`，实际写入与调度计划都是本地槽 20002。每卡持有 2 个 KV heads，写后 K/V 形状为 `[2, 256]`。第 0 层在本地行 14 更新两种状态，卷积形状为 `[3, 5120]`，递归形状为 `[24, 128, 128]`。每个相邻前向逐卡核验了 48 对卷积及 48 对递归状态的承接。两模型的缺文件副本均能定位 rank 1 的损坏快照并读取 rank 0。另在 Qwen 的副本中改写一份 rank 1 的 msProbe 原生张量，同卡缓存快照仍可读取，返回值按 msProbe 摘要异常标记 incomplete。


Llama 正式矩阵之后，以与 Qwen 相同的最终 worker 适配器再次验证一轮 prefill 和 decode，两卡均读回写后 K、HCCL 组、宿主 PID 及分页缓存读源。本次运行使用的服务全部经公开 stop 入口停止。


本次拆分仅交付 `verify-agent` 管理的运行时采集工具；完整使用说明、输入输出及限制唯一维护在[工具说明](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md)。无需 GPU 基线解析或评测 SDK，调用方可以直接控制单条服务请求。

## 代码归属

| 模块 | 职责 |
| --- | --- |
| `verification/request.py`、`runner.py`、`lifecycle.py` | 可选采集准备、部署回执及最终服务交接 |
| `verification/runtime/capture/contract.py`、`prepare.py`、`probe.py` | 模式和配置检查、挂载映射、目标解释器探针及读写证明 |
| `capture/launch.py`、`adapters/` | 启动适配器及实际前端、引擎、worker 挂点；独立复制 logits/分数 |
| `capture/service.py`、`lease.py`、`session.py`、`session_receipt.py` | 部署与进程身份、互斥、逐请求授权及封存 |
| `capture/index.py`、`tensors.py`、`action.py` | 原生调用索引、覆盖和摘要校验、独立诊断与离线查询 |
| `tests/vllm-model-migration/worker_capture/` | 准备、适配器、会话、覆盖与不依赖评测器的公开闭环 |

普通部署不声明 `worker_capture` 时兼容原契约。采集准备不可用单独报告，不掩盖普通功能失败；取消和最终证据写入失败沿用原资源清理规则。采集成功不等于精度对齐或性能达标。

## V1 冷前缀完整请求

在原有全层 worker 上，显式启用 `full_request` 可将每个实际调度的 prompt 块或 decode 步连接到一个独立的 `forward_id`，再关联对应的 msProbe step/rank 和逐层快照。前向索引记录该步消费的输入、半开位置区间、可能为空的输出序号。普通注意力核对逐位置写槽和 K/V 原值，GDN 核对块首尾物理状态和相邻块承接。查询方式、预算和计时口径见[完整请求契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#从完整请求回查每次前向)。

2026-11-23 以 Llama-3-8B-Instruct 原始权重完成两组真实 NPU 对照。9 个 prompt Token 的整段请求产生三个输出 `[19, 11, 220]`。33 个 prompt Token 实际分成 32+1 两块，产生 `[18, 11, 220]`；第一块没有输出，第二块产生输出 0，再经两次 decode 生成输出 1 和 2。每次前向均核对 32 层，两个用例的缓存观测开关输出分别一致。首块普通注意力直接使用本轮 K/V，后续单 Token 的 prompt 末块使用分页缓存。两组分别保存 8.25 和 26.25 MiB 缓存快照，开启观测时 msProbe 原生文件分别为 210,375,116,604 和 288,745,332,474 字节。

Qwen3.5-27B 的 9 Token 整段请求产生 `[19, 11, 220]`，34 Token 请求实际分为 32+2 两块，产生 `[11, 220, 16]`。每组的观测开关输出相同，每次前向均有 16 层普通注意力和 48 层 GDN。分块请求的两次 GDN prefill 均执行分块递归算子；首块 `has_initial_state=false`，续块为 `true`。48 层的卷积和递归状态在这两个块之间共核对 96 组写后与下次更新前的摘要。整段和分块分别保存 1,040 和 1,512 MiB 缓存快照，开启观测时 msProbe 原生文件分别为 390,393,271,583 和 527,027,850,696 字节。上述数字均为三个输出 Token 的请求总量。

## V1 单次 decode 的缓存采集

### 按实际层结构联合采集

开启 `decode_cache` 后默认遍历已加载模型的文本解码层。模型预检给出各子配置声明的层数与类型，worker 给出实际运行的层路径、子模块、状态后端和结构图。msProbe 采集对应层的模块及注册 API 调用。mindie-debugger 单独记录每次层调用进出的首个浮点激活，并按状态后端采集缓存。普通注意力保存 scheduler 分配、算子有效读范围、写槽以及待写和写后 K/V；GDN 保存卷积和递归状态的选行、更新前后值。FIA 的读范围来自算子入参，实际数值来自被观测张量。每层有独立的调用序号、文件摘要和完整性结论。

对同一输出 Token 查询 `layer_index` 即可选层。出现未知后端、漏挂点或快照预算耗尽时，回执列出受影响的层。显式选择 `legacy_pair` 可复现下文的两层示例，原有回执仍可读取。操作示例见[工具说明](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#从一次-decode-回查层缓存)。

2026-11-23，Qwen3.5-27B 真实 NPU worker 在第二个输出 Token 的同一次 decode 中封存 64 个层调用。16 层普通注意力、48 层 GDN 均取得状态数据，msProbe 对应 756 次模块调用及 3188 次注册 API 调用。层边界快照共 1,310,720 字节，状态快照共 308,019,200 字节，均按原 dtype 保存。第 3 和第 63 层该次写槽均为 7700，第 0 和第 62 层的 GDN 状态行分别为 6 和 8。槽号和状态行只适用于该次调度。

同一份 Skill 在 Llama-3-8B-Instruct 真实权重上识别并封存了 32 个普通注意力层。该次 decode 对应 388 次模块调用、516 次注册 API 调用。第 31 层的有效读槽区间为半开区间 [256, 275)，写槽为 274。每层的 K/V 读写计划、待写数据和写后值可分别回查。

两个模型分别使用单卡服务，输入均为 `1, 2, 3,`，msProbe 始终开启。两种缓存观测设置下输出 Token ID 均为 `[19, 11]`。使用公开 `capture-read` 按 `output_index=1` 读取后，逐层核对了 msProbe 调用、读写或状态行计划、实际写入及张量文件摘要。Qwen 返回 384 份层边界和状态快照，Llama 返回 192 份。两次服务均已通过公开 stop 停止。

### 两层示例的首次开发记录

`adapters/decode_cache/` 在现有 worker 前向内关联 scheduler 完整分配、层算子参数和 mindie-debugger 内存观测。`cache_evidence.py` 负责将原始数值、模块调用和已接受 Token 绑定到同一个前向。示例选择第二个输出，输入 16、绝对位置 20、输出 17。合成页大小 16、算子块大小 4 时，第 3 层预期与实际写槽均为 52，第 0 层卷积和递归的目标状态行均为 7。地址与数值来自本次 CPU 用例。读实参给出的范围不提供设备访存追踪。

已在 CPU 上运行固定 vLLM-Ascend 0.20.2rc1 源码中的 `reshape_and_cache`、`_get_fia_params`、`forward_fused_infer_attention` 和 GDN `_forward_core` 的 Eager 分支，设备算子使用 CPU 替身。使用 mindie-debugger 0.1.4 的 `MemoryWatchpoint` 和 msProbe 源码的线程内采集开关，核对四个调用边界和写后数值。公开 CLI 的独立回归覆盖真实 CPU 张量、错槽、状态行错位、缺算子、缺依赖、预算耗尽、提前 EOS、前向身份错误及离线文件被改写。目标 NPU 的算子执行、设备格式与观测开销待实测。用法和字段见[缓存采集契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md#从一次-decode-回查层缓存)。

## 2026-11-17 离线验收

专项命令：

```bash
python3.12 -B -m unittest discover -s tests/vllm-model-migration/worker_capture -p 'test_*.py' -v
```

42 项通过。新增 9 项公开闭环测试在完整资源的临时副本中运行真实 CLI、生产部署交接校验、一次本机 HTTP 请求、封存和离线查询。仅目标 Linux 进程观察与实际 worker 推理由显式替身替代；评测 SDK 导入被拒绝。覆盖第二个输出 Token、不同实际 ID、完整词表、全部合成层、重复 API、重复 Token、无输出前向、提前 EOS、漏采、摘要篡改及进行中的请求租约。已有测试另验证实际适配器逻辑与 Docker/process 准备交接。

全量离线回归 476 项、source_contract 专项 52 项通过，均无跳过。结构审计为 0 errors、194 warnings，原 dev 为 177 warnings。本次增加的软提示对应有界采集校验函数及两个目录；最高复杂度 15，未超硬阈值。请求/身份/调用覆盖校验分别保留在所属边界，未增加跨模块调度或共享杂项包。基线由现有生成器登记新增文件并收紧 cleanup 指标；未修改策略、阈值或添加豁免。

原始改动中的 GPU 基线解析、NPU 按基线复测、EvalScope 控制器、评测样本映射和其相关契约均不在本提交。测试替身不证明真实模型/NPU、任意版本、图模式、多 rank、并发或设备 kernel 覆盖；本轮未运行服务器任务或安装依赖。

## 2026-11-17 实机闭环修复

真实部署暴露了三个离线替身未覆盖的问题：bootstrap 的脚本目录使内部 coverage.py 遮蔽 numba 的可选第三方依赖；capture-session 在新输出目录创建前写入身份核验日志；目标镜像的 chat serving 源码尚未登记适配。修复沿用独立采集工具边界，不引入基线或评测器流程。

| 修改位置 | 责任与验证 |
| --- | --- |
| adapters/bootstrap.py | 主进程和 spawn 重放时隔离适配器脚本目录；真实子进程回归覆盖 module/cli 启动以及 spawn/fork，保留调用参数和用户依赖路径 |
| session.py | begin/arm/seal/status 的身份核验在各自独立目录写日志；新 work-root/run-id 可直接使用，失败保留日志但不生成租约 |
| adapters/sources.py | 审核目标 chat serving 的非流式 generate 参数和最终响应路径后纳入准确摘要；保留未知字节拒绝测试，具体协议见工具说明 |
| adapters/eager_calls.py、worker.py | 已审核的编译装饰器直接 Eager 调用仅在采集活动时进入标准模块 hook；已有用户 hook、未知源码、模式变化拒绝，不豁免外层模块覆盖 |
| tensors.py | 保留原生记录中实际出现的 None 和空容器返回值；缺少输入/输出字段仍拒绝，不从输入字面量补造输出 |
| tests/worker_capture/ | 设备观察替身也执行真实日志文件写入，避免掩盖目录顺序；挂点协议片段固定单输入、greedy、非流式条件，并明确省略的 SDK 业务分支 |

测试先在原实现上复现失败，再验证修复：52 项采集专项、486 项全量离线测试、52 项 source_contract 通过。结构审计 0 errors、194 warnings；bootstrap.main 语句数 11→6、session.begin 19→17，按生成器收紧基线并登记测试；没有修改阈值或添加例外。适配器初始化和会话观察目录仍由原模块负责，不增加跨 Skill 依赖。

首轮真实封存还发现 `language_model.model` 的编译装饰器直接调用 forward，64 个 decoder 层已落盘但外层模型容器缺一次输入输出；严格覆盖检查拒绝该回执。新增直接 Eager 调用适配后，先用目标镜像中的实际装饰器和 msProbe 做 CPU 协议验证，确认模块输入输出落盘、数值不变，再重新部署真实模型进行 NPU 验收。

后续真实采集已覆盖该外层模型容器，但封存发现赋值 API 的 `[null]` 和 kernel 启动 API 的空输出结构未被解析器保留。修复按原生结构记录这些返回值，并保留缺字段的拒绝回归；这不提供额外的写回后缓冲区内容。失败会话保留原始状态，以新的公开采集会话验收修复，不回写失败回执。

最终真实 NPU 验收通过。Qwen3.5-27B 使用 BF16、单 rank、同步 Eager；vLLM 0.20.2+empty、vLLM-Ascend 0.20.2rc1、msProbe 26.0.0。公开部署完成 42 项功能用例及独立语义复核；调用方随后经过非流式 Chat、capture-session begin/arm/seal/end 和 capture-read，未读取 GPU 基线或调用 EvalScope。

关闭与开启采集的输出均为 `[16,17]`。查询 `output_index=1` 得到 Token 17、实际 prompt 加 Token 16 的完整前文、decode 输入 `[16]` 和位置 `[20]`。两份全词表向量各含 248320 项：原始 logits 的 argmax 为 11，按本次允许词表处理后的 argmax、sampler 候选和引擎接受值均为 17。它们关联同一个 forward、step=3、rank=0；该请求仅用于验证选词处理和关联，不作为模型语义成绩。

该前向的 64 个 decoder 层、756 次模块调用、29 种受支持 API 的 3188 次调用均通过独立计数与原生记录的覆盖核对。公开查询复核了全部引用摘要；另外在目标环境使用 PyTorch CPU 加载全部 10900 个非空输入输出张量，核对 shape、dtype 和切片边界，16 个空张量保留显式结构。另行打开 578 个原生模块参数文件，合计 11478 个文件、131177623722 字节。参数文件另计，不冒充 I/O 索引引用。

覆盖范围是本次文本前向实际执行的模块和 msProbe 注册 API；未执行的视觉分支、设备 kernel 内部数据、图模式、多 rank 和并发没有因此获得验收。验收结束后公开 stop 通过，服务容器已退出；停止回执独立保存，原部署交接摘要保持不变，原始张量和失败尝试均保留。
