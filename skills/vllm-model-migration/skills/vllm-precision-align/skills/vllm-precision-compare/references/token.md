# Token 预测对齐

`scripts/compare.py --action token --request /abs/request.json --output /abs/comparison.json` 比较同一题同一回答的 GPU、NPU 记录。也可用 `--work-root` 和 `--run-id` 写入独立尝试。请求无效、顶层 GPU 或 NPU 样本文件无法核验、输出冲突时返回 2。交付报告时返回 0，业务结论读 `result`。若比较到某个输出位置时向量缺失或摘要错误，报告给出 `insufficient_evidence` 和一基的 `first_missing_token`。`--plan` 静态检查请求和输出位置，可在尚未取得预测记录时使用。

## 输入

- `token_alignment.raw_logits.atol` 和 `token_alignment.processed_scores.atol` 都是必填的有限非负绝对容差，0 表示逐候选相等。
- 阶段用 `case={subset,sample_id}` 传入经过两端预测包核验的选题身份。单题文件可省略 `subset`，已填写的子集和题号须与清单一致。独立比较省略 `case` 时，两端记录自行提供一致的样本身份，遇到跨子集重号须填写 `subset` 区分题目。
- `gpu` 和 `npu` 各为样本记录的 `{path,sha256}`。两端各保存 `sample_id`、`prompt_token_ids`、`raw_logits`、`processed_probs`、`output_token_ids` 和 `stop_reason`。合成数据应将 `evidence_kind` 标为 `synthetic`。报告逐来源保留此标识，未提供时标为 `unspecified`。
- 每生成一个 Token，`raw_logits` 和 `processed_probs` 各保存一行全词表向量，列索引就是有效词表的 Token ID。每行可以是数值数组，或 `{path,sha256,dtype,size}`。向量引用为相对样本文件目录的路径、二进制文件 SHA256、数据类型和候选数。二进制使用小端连续 `float16/bfloat16/float32/float64`，不含文件头。`processed_probs` 保存选词规则处理后、选出 Token 前的完整归一化分布，取值范围为 [0,1]。贪心时对实际选词前分数应用 softmax。数组行 `null` 表示该位置缺失。写入方须导出完整词表。
- 可选 `cpu` 提供已有 CPU 单点参考的固定引用，其文件包含 `sample_id`、`prompt_token_ids`、包含 prompt 的完整 `prefix_token_ids`、一基 `position`、一维 `raw_logits` 和 `processed_probs`，可标注 `subset` 和 `evidence_kind`。输入范围、题目、前文和位置均须与双端首疑点一致。
- 可改用 `cpu_model` 在本地独立复算。提供冻结的模型 `root`、相对文件名到 SHA256 的 `files`、实际 `weight_files` 和精度评测协议的 `model_identity`，沿用原生测量对文件清单规范 JSON 的 SHA256。可指定 `dtype=float32|float64`。仅当上游实际有效的选词规则证据能证明使用无额外处理的贪心时提供 `plain_greedy=true`。未证明时仍核验 CPU 原始 logits，并将 CPU 处理后概率标为未评估。

## 判定与输出

1. 核对样本身份及逐项输入 ID。输入不同给出首个输入差异位置。相同输入时，第一行对应 prompt 最后一个有效位置预测的首个输出。
2. 逐步比较全词表两组分数的最大绝对差与各自容差，以及两端实际接受的 Token ID。差值等于容差时通过。遇到首个异常即结束向量读取，报告该位置实际失败项、候选 ID、两端数值和阈值。该步选词不同仍具有相同前文。随机采样出现不同接受 ID 时保留真实行为差异。
3. `first_anomalous_token` 从 1 计数，`covered_tokens` 为已经逐步核实的输出数。`aligned` 表示两端完整有效、逐步通过且停止原因相同；`mismatch` 表示可比位置的分数或接受 ID 首次不同。`early_stop` 标出一方已经结束、另一方仍有输出的首个位置。`stop_reason_mismatch` 使用结束边界 `stop_after_tokens`，异常 Token 为 null。`input_mismatch` 表示无法比较同一输入。`insufficient_evidence` 表示无法确认首异常或完整通过；前面缺步时返回一基 `first_missing_token`。第一处已证实的异常后方有缺项仍保留该异常。
4. `cpu_check` 仅在双端已经找到具有预测分数的首个疑点时读入参考或运行本地 CPU。独立前向显式运行在 CPU，使用本地 Transformers 原生因果模型、相同的 safetensors 权重和 CPU FP32/FP64。复核前检查文件内容和权重索引，默认 FP32；配置指明 FP64 时沿用 FP64。复核时仅送入实际前文，取得最后一行 logits。能够重建无额外处理的贪心规则时以 FP64 softmax 生成参考概率。`cpu_check` 保存位置、每端相对 CPU 的两类最大绝对误差与容差结论，使用本地模型时同时注明权重身份、实际计算类型和软件版本。所需依赖、有效权重或支持的模型实现不可用时，CPU 项单独记为未评估。合成 CPU 对照供比较算法验证，设备精度证据须取自实际运行。

外层 `evidence` 绑定请求和已核验的数据文件。返回的 `validity` 表示双端比较是否具备判定当前结论的有效数据；CPU 核验与各来源的实际硬件身份在各自字段中记录。

两端可记录 `eos`、`length`、`stop_token:<id>` 或 `stop_string:<text>` 等有明确依据的原因。只有粗粒度 `stop` 或 `unknown_stop` 时，仍能比较已生成位置和输出长度；若完整一致结论依赖区分 EOS、指定停止词或指定 Token，则记为证据不足。
