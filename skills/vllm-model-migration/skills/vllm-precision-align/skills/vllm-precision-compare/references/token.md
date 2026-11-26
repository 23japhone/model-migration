# Token 预测对齐

`scripts/compare.py --action token --request /abs/request.json --output /abs/comparison.json` 比较同一题同一回答的 GPU、NPU 记录。也可用 `--work-root` 和 `--run-id` 写入独立尝试。请求无效、顶层 GPU 或 NPU 样本文件无法核验、输出冲突时返回 2。交付报告时返回 0，业务结论读 `result`。若比较到某个输出位置时向量缺失或摘要错误，报告给出 `insufficient_evidence` 和一基的 `first_missing_token`。`--plan` 静态检查请求和输出位置，可在尚未取得预测记录时使用。

## 输入

- `token_alignment.raw_logits.atol` 和 `token_alignment.processed_scores.atol` 都是必填的有限非负绝对容差，0 表示逐候选相等。
- 阶段用 `case={subset,sample_id}` 传入经过两端预测包核验的选题身份。单题文件可省略 `subset`，已填写的子集和题号须与清单一致。独立比较省略 `case` 时，两端记录自行提供一致的样本身份，遇到跨子集重号须填写 `subset` 区分题目。
- `gpu` 和 `npu` 各为样本记录的 `{path,sha256}`。两端各保存 `sample_id`、`prompt_token_ids`、`raw_logits`、`processed_probs`、`output_token_ids` 和 `stop_reason`。合成数据应将 `evidence_kind` 标为 `synthetic`。报告逐来源保留此标识，未提供时标为 `unspecified`。
- 每生成一个 Token，`raw_logits` 和 `processed_probs` 各保存一行全词表向量，列索引就是有效词表的 Token ID。每行可以是数值数组，或 `{path,sha256,dtype,size}`。向量引用为相对样本文件目录的路径、二进制文件 SHA256、数据类型和候选数。二进制使用小端连续 `float16/bfloat16/float32/float64`，不含文件头。`processed_probs` 保存选词规则处理后、选出 Token 前的完整归一化分布，取值范围为 [0,1]。贪心时对实际选词前分数应用 softmax。数组行 `null` 表示该位置缺失。写入方须导出完整词表。
- `cpu_model` 指向首轮部署冻结的本地模型。提供模型 `root`、相对文件名到 SHA256 的 `files`、实际 `weight_files` 和 `model_identity`（规范文件清单的 SHA256）。`dtype=float32|float64` 可指定起始计算精度。阶段根据已固定的模型与测量回执自动组装这些字段。组装失败时用可选的 `cpu_model_error` 传递具体原因。旧请求或预测包中的 `cpu` 记录仅作历史输入兼容，本轮 CPU 结果始终来自现场复算。
- 只有首轮实际选词参数能证明使用无额外处理的贪心时才设置 `plain_greedy=true`。其他情况下仍可复算 logits，处理后概率单列缺项。

## 判定与输出

1. 核对样本身份及逐项输入 ID。输入不同给出首个输入差异位置。相同输入时，第一行对应 prompt 最后一个有效位置预测的首个输出。
2. 逐步比较全词表两组分数的最大绝对差与各自容差，以及两端实际接受的 Token ID。差值等于容差时通过。遇到首个异常即结束向量读取，报告该位置实际失败项、候选 ID、两端数值和阈值。该步选词不同仍具有相同前文。随机采样出现不同接受 ID 时保留真实行为差异。
3. `first_anomalous_token` 从 1 计数，`covered_tokens` 为已经逐步核实的输出数。`aligned` 表示两端完整有效、逐步通过且停止原因相同；`mismatch` 表示可比位置的分数或接受 ID 首次不同。`early_stop` 标出一方已经结束、另一方仍有输出的首个位置。`stop_reason_mismatch` 使用结束边界 `stop_after_tokens`，异常 Token 为 null。`input_mismatch` 表示无法比较同一输入。`insufficient_evidence` 表示无法确认首异常或完整通过；前面缺步时返回一基 `first_missing_token`。第一处已证实的异常后方有缺项仍保留该异常。
4. 已发现双端首差时，CPU 重新加载冻结的本地 safetensors 权重，用 prompt 和首差前两端共同接受的 Token 做一次前向。它读取最后一行 logits，可复现贪心规则时同时计算 FP64 softmax。每次执行比较均重新计算该位置。起始精度来自显式配置，省略时 FP64 模型从 FP64 开始，其他模型从 FP32 开始。遇到不支持的计算类型时按 FP64、FP32、BF16、FP16、当前环境提供的 FP8 类型依次尝试。缺少权重或依赖时直接记录原因。
5. `cpu_check.status=completed` 给出位置、实际采用的 `dtype`、参数类型、输出类型、模型身份、软件版本和尝试记录 `attempts`。GPU、NPU 分别记录相对 CPU 的最大绝对误差与 `within_tolerance`。仅取得 logits 时，`processed_scores.status=unavailable`。独立的 CPU 子进程保存本次尝试记录。权重加载失败、子进程异常退出或超出单题复算时限时，写 `cpu_check.status=unavailable`、原因、精度尝试和空的两端误差。单题维持 `result=mismatch`，可以交给第三阶段定位。没有双端首差时，`cpu_check.status=not_applicable`。

外层 `evidence` 绑定请求和已核验的数据文件。返回的 `validity` 表示双端比较是否具备判定当前结论的有效数据；CPU 核验与各来源的实际硬件身份在各自字段中记录。

两端可记录 `eos`、`length`、`stop_token:<id>` 或 `stop_string:<text>` 等有明确依据的原因。只有粗粒度 `stop` 或 `unknown_stop` 时，仍能比较已生成位置和输出长度；若完整一致结论依赖区分 EOS、指定停止词或指定 Token，则记为证据不足。
