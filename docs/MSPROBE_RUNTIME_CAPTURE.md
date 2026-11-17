# msProbe 运行时采集交付

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
