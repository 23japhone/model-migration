# 从首个异常 Token 对齐张量与算子

精度阶段的入口为 `scripts/precision.py --action tensor`。客户配置、外部 GPU 采集和独立复算都使用同一份按题冻结的计划。适用范围为双端单卡、单请求、同步 Eager、文本贪心生成。阶段只取共同可见的模块和 API 的前向输入、权重、状态和输出。

## 客户任务与独立入口

在已有质量及 Token 任务的 `task.json` 加入 `accuracy.tensor_alignment`。例如取 `{"atol":0.001,"rtol":0.0001}`，或用 `{}` 只报告浮点误差。省略时按已有配置执行。第二遍的 `accuracy.token_alignment` 继续使用自己的两项绝对容差。已有与本次质量报告匹配的外部 Token 报告时，也可随上下文提供。

独立调用的请求包含 `schema_version="1.0"`、`quality_report`、`token_report`、`tensor_alignment` 和数组 `evidence`。两个报告及数组项使用 `{path,sha256}` 引用。需要本地 NPU 采集时再给首轮冻结的 `native_request`。命令示例：

```bash
python3 scripts/precision.py --action tensor --request "$REQUEST" --work-root "$WORK_ROOT" --run-id "$RUN_ID"
```

只有第一遍失败范围内、第二遍两端都有预测且比较结果为 `mismatch` 的题进入取证。目标是第 `p` 个输出的预测行，前文为 prompt 加前 `p-1` 个实际接受 ID；分块 prefill 只取包含最后一个有效 prompt 位置的前向。两端的物理 forward_id 可不同。相同选词下的 logits 或概率超限同样触发采集。单侧提前结束、仅停止原因不同和输入不一致在报告中单列。

## 冻结共同范围

随 `context.evidence` 提交一个 `tensor-scope-v1` 双端映射，或分别提供两端的 `tensor-inventory-v1` 可观察调用清单。清单可从实际模型模块、已绑定的 API 名和已核验签名整理。每个模型用首轮协议的 `model_identity`。异构融合按已经核对的共同输入输出边界映射。重名调用各用独立逻辑 ID 与从零计数的层内 `occurrence`。没有可证明的映射时，阶段返回 `needs_scope`。

以下是一项模块映射的 `calls[]` 示例。真实 scope 还应写入 `runtime_versions`，分别记录两端的 `torch`、`vllm` 与 `msprobe` 版本。模型身份、原始质量报告、单题比较、预测记录、目标位置、前文和共同清单都进入单题计划。

```json
{
  "id": "layer0.proj", "kind": "module", "occurrence": 0,
  "gpu": {"owner": "model.layers.0", "name": "proj"},
  "npu": {"owner": "model.layers.0", "name": "proj"},
  "slots": [
    {"id":"x","role":"input","gpu":["input_args",0],"npu":["input_args",0]},
    {"id":"w","role":"weight","gpu":["parameters","weight"],"npu":["parameters","weight"]},
    {"id":"y","role":"output","gpu":["output",0],"npu":["output",0]}
  ],
  "cpu_reference":{"op":"linear","inputs":{"x":"x","weight":"w"},"output":"y"}
}
```

该示例适用于已经确认无 bias 的线性层。实际含 bias 时把它加入两端槽位和 CPU 实参。每个 `tensor-plan.json` 都由阶段生成，使用该文件的原始字节和 SHA256 启动采集。报告中的 `scope_uncovered` 提供已知未配对调用。关键字参数、标量、原地写后的参数、历史状态以及有效区域应在清单中逐项表达。只有已证明对目标行独立的张量才截取该行。注意力依赖的历史 K/V 需保留其逻辑范围。

## 客户 GPU 采集与交付

在客户原有 vLLM 评测环境安装已经核对过的 msProbe，将本包 `verify-agent/scripts` 和发布目录的 `skills/vllm-model-migration/scripts` 加入 worker 的 Python 模块搜索路径。在单请求评测代理已经把稳定题号、服务 trace 和 worker 内部请求 ID 绑定之后，使用下述上下文包住同一请求的整个 execute/sample 周期。适配器针对 vLLM V1 的同步 GPUModelRunner 接口，按真实调度、已接受前文、logits_indices 和采样结果定位前向。模型身份取实际加载权重清单的首轮协议摘要。

```python
from verification.gpu_capture import capture_request

with capture_request(runner, plan_ref, new_output_dir, case=case_key,
                     model_files=baseline_weight_and_config_sha256_map,
                     trace_id=http_trace_id, internal_id=engine_request_id) as bundle_path:
    run_bound_request()
```

`runner` 是已经加载模型的 worker 实例，`run_bound_request` 是客户现有的单题重放入口。输出目录必须是新目录。包内 `capture_seconds` 记录目标前向探针运行时长，`runtime` 交付的 `tensor_bytes` 与 `prediction_bytes` 分别记录实际张量和预测向量字节。采集端在目标前向安装 msProbe L0/L1 回调，筛选调用与槽位后才复制张量，保存真实 `.pt`、目标步两个预测向量、实际接受 ID 及带摘要的原生调用索引。在 GPU 机器外处理时，整体复制单题输出目录，保留内部相对路径。将 `tensor-bundle.json` 在当前机器上的绝对路径及 SHA256 加到 `context.evidence`。每题每端只提交一个有效包。包格式和 CPU 能力见[离线比较契约](../skills/vllm-precision-compare/references/tensor.md)。[msProbe 官方 vLLM 支持范围](https://github.com/Ascend/msprobe/blob/2a7355a05c76604b0624bdda096ae9b05936b01a/docs/zh/user_guide/dump/vllm_dump_instruct.md)

NPU 端核对 GPU 材料后，通过 `validator.py --action tensor-run` 重用冻结的原生评测请求和单卡采集会话。目标步使用同一份清单；L2、MindIE Debugger 和缓存观察器关闭。原生测评回执和本次单题评分独立留档。实际 NPU 部署还需具备同步 Eager worker 的采集能力。[NPU 采集契约](../../vllm-migration-execute/skills/verify-agent/references/runtime-capture.md)

## 续接与报告

沿用原 run-id 和 `migrate.py --task … --context … --resume <上次客户回执> …`。只改容差时同时更新上下文的 `task_source` 摘要。已完成交付且上下文只补三遍材料时，客户入口直接使用已冻结的上游回执。也可独立调用阶段或只读比较器做离线复算。

`customer-manifest.json` 的 `tensor_alignment[]` 指向各数据集的报告。查看 `cases[]` 中的目标位置、清单、GPU/NPU 核验、目标步能否复现、比较回执和缺项。已证实超限的调用保留其坐标、双端原值与 CPU 误差；上游输入已漂移时，输出差异用于定位传播路径。只在所需操作数、等价语义和 CPU 参考齐备时给出 CPU 结论。

第三遍在两端分别核对同一题、同一前文和目标位置的第二遍记录。接受的 Token ID 须相同，原始 logits 和处理后概率使用第二遍的容差。任一端未复现时，查看该端比较回执；GPU 预测未复现会在启动 NPU 重跑前结束该题。提交的材料无法绑定题目时，可从报告的 `uncovered` 查询文件引用及原因。

无设备的验证方式是在仓库根运行以下命令。生成器构造第一遍质量证据、两端第二遍预测、正常与人工扰动的 GPU 张量、对照端张量，并实际调用公开的 Token 和 Tensor 阶段。输出根需要使用新目录。

```bash
python3.12 -B tests/vllm-model-migration/tensor_stage_cases/build_tensor_stage_fixture.py --output "$NEW_FIXTURE_DIR"
```

查看输出的 `fixture-result.json` 和关联报告。模拟输入保留 `evidence_kind=synthetic`，阶段汇总中的 `simulated` 和 `comparison_result` 分别说明来源及数值结果。
