# msProbe 运行时采集工具

本工具属于 `verify-agent`，直接消费部署验证交接。调用方可以是自己的 Python 程序；无需 GPU 基线、数据集、EvalScope 或 AISBench。完整请求约束见[公开契约](contract.md#msprobe-采集准备)。它提供采集和反查，不判定模型精度是否达标。

## 输入、输出和执行位置

| 环节 | 必需输入 | 可交给下一环节的输出 |
| --- | --- | --- |
| `verify` 准备并启动 | 原部署请求 + `worker_capture`，已声明可写输出路径 | 验证报告、最终部署交接、采集准备回执和配置摘要 |
| `capture-session begin` | 验证报告 `{path,sha256}`、调用控制进程 `owner_pid` | 不可变 `session={path,sha256}`；持久租约 |
| `capture-session arm` | session 引用、`sample_key`、`native_id`、唯一 `trace_id` | 活动请求目录；尚未发送模型请求 |
| 调用模型 | 一条带 `X-Precision-Trace: <trace_id>` 的真实请求 | 调用方保存的原始 HTTP JSON 请求/响应字节及摘要 |
| `capture-session seal` | session 引用、trace_id、`http.request/response` 摘要引用 | `receipt={path,sha256}`、实际 Token 数、样本身份 |
| `capture-session end` | session 引用 | 封存清单、释放本次租约；模型服务继续原生命周期 |
| `capture-read` | 采集 receipt、request_id=trace_id、零基 output_index | 前文、预测向量、接受 Token、前向和完整调用文件索引 |

所有动作通过同一个 `scripts/verify.py`，保留 `--request --work-root --run-id --plan`。`--plan` 只做静态检查，不验证来源材料、进程或设备。真实调用在部署的 Linux 宿主侧执行；Docker 使用已有显式可写 bind mount，配置同时记录宿主路径与容器路径。调用方与运行材料必须在同一可见路径空间；不存在自动 SSH 或跨机文件传输。

适用自有、retained、单 rank、单请求、同步 Eager、文本、greedy 的服务。完整总 Skill 资源必须可访问，既有共享设备合同是依赖；单独复制 `capture/` 不足以部署。目标解释器必须已经安装兼容 msProbe。工具核对适配器源码、目标 vLLM/vLLM-Ascend 源码及挂点，不自动安装、升级或改变服务并行/图模式。`prepared` 仅证明准备成功，开始会话还需实际 worker、engine、frontend 就绪记录及当前服务身份。

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

## 验收边界

独立离线测试覆盖公开 CLI、部署摘要交接、本机 HTTP 请求、会话租约、逐 Token 封存和离线反查，并以显式替身代替 Linux 设备观察及 worker 推理。测试使用两层合成模型、完整合成词表和合成张量字节，不是模型实测。真实目标镜像仍需核对源码、挂点和进程身份；在真实 NPU 上重新打开 msProbe 张量、核对开关采集的输出及开销后，才能报告该环境实机通过。


## 启动隔离与目标源码核验

适配器 bootstrap 在主进程及 spawn 重放时移除自身脚本目录的顶层搜索入口，只保留包根；内部的 `adapters.coverage` 不应被第三方 `import coverage` 误认。不需要调用方设置 `PYTHONSAFEPATH` 或额外挂载注册源码来规避同名依赖。

begin 在第一次身份观察写日志之前创建独立尝试目录。arm、seal 和 status 的身份观察也各自使用新的目录，允许调用方使用尚不存在的 work-root/run-id；失败的观察日志保留，不创建成功租约或覆盖旧尝试。--plan 保持零写入。

Chat 前端另审核了源文件 SHA256 `8c41af967ef813bf6fd2e2f16b8eedbbf6098d78c971639a58b6297d1c217c02`：`create_chat_completion(request, raw_request)` 的非流式路径从 `raw_request` 读取 trace，调用 `engine_client.generate(engine_input, sampling_params, sub_request_id, ...)` 并等待最终响应。适配器继续读取实际 generate 参数和响应 ID，不自行重建 chatcmpl ID。离线回归使用该版本真实挂点协议摘录检验位置参数、关键字参数和最终输出绑定；未登记的源码字节仍拒绝。流式、beam、多请求和非 greedy 采集不在此范围。目标实机结果仍必须由该次部署、采集回执和张量读取证据证明。

部分 vLLM 模型的编译装饰器在 `do_not_compile=True` 时直接调用 `forward()`，绕过 PyTorch 模块 hook。适配器核验装饰器源码摘要后，仅在采集活动期间将目标实例的这一分支接到 PyTorch 标准模块调用，确保外层模型容器也产生 msProbe 输入输出；关闭采集和其他实例保留原调用。就绪记录的 `direct_eager_modules` 列出这些模块。已有局部或全局用户 hook、未知源码或执行模式变化会阻塞该适配，不删减缺失模块来宣称覆盖完整。
